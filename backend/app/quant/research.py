"""Pesquisa: backtest + walk-forward de todas as estratégias (escopo HISTORICAL/RECENT).

- Dados: candles da conta REAL (somente leitura), um estudo por timeframe (1h, 15m, 5m). Este módulo NÃO importa
  nada de execução. O regime do BTC é sempre o do 1h.
- Filtro de custo igual ao do motor: o stop precisa valer N× (taxas de ida e volta + spread estimado).
- Cada estratégia é testada isoladamente (um trade por estratégia/par por vez), com as MESMAS funções usadas ao vivo.
- Entrada na abertura do candle seguinte ao sinal; stop/alvo mantêm a distância percentual do sinal.
- Custos: taxa 0,05% por lado + slippage/spread 0,05% por lado + funding estimado 0,01% a cada 8h (sempre contra).
- Stop com gap: se o candle abrir além do stop, a saída é no preço de abertura (pior que o stop).
- Mesmo candle tocando stop e alvo: assume o STOP (conservador).
- Saídas do motor também simuladas: TIME_EXIT, MOMENTUM_LOSS, REGIME_CHANGE, EXIT_SIGNAL.
- Walk-forward: parâmetros são FIXOS (nada é otimizado). O período é dividido em 4 janelas cronológicas:
  (a) estabilidade = fração de janelas com expectativa positiva;
  (b) seleção ancorada = em cada janela k, só entram as estratégias positivas nas janelas anteriores; o resultado
      fora da amostra dessa regra de seleção é reportado.
- Simulação do MOTOR completo (confluência + score + mínimos atuais) também é reportada.
Viés conhecido: o universo é o top-N por volume de HOJE (viés de sobrevivência/seleção). Isso é informado no relatório.
"""

import threading
import time
from collections import defaultdict

from sqlalchemy import select

from .. import botlog
from ..common import get_config
from ..database import session_scope
from ..models import utcnow
from ..scanner import NON_CRYPTO
from . import data, events, performance, registry
from . import features as F
from .confluence import ConfluenceGroup, LEVEL_RANK
from .exits import HOSTILE
from .features import FeatureView
from .models import QuantConfig, ResearchRun
from .regime import RegimeSeries
from .scoring import score_group

MODULE = "quant.research"
HOUR = 3_600_000
FEE = 0.0005
SLIP = 0.0005
FUNDING_8H = 0.0001
WARMUP = 210
FOLDS = 4
_running = threading.Lock()


def fetch_history(symbol: str, interval: str, bars: int) -> list[dict]:
    step = {"5m": 300_000, "15m": 900_000, "1h": HOUR, "4h": 4 * HOUR}[interval]
    out: dict[int, dict] = {}
    end = None
    while len(out) < bars:
        page = data.market().get_klines(symbol, interval, 1000, end_ms=end)
        time.sleep(0.25)   # respeita o limite de requisições (evita bloqueio de IP)
        if not page:
            break
        new = 0
        for k in page:
            if k["time"] not in out:
                out[k["time"]] = k
                new += 1
        if new == 0:
            break
        end = min(k["time"] for k in page) - step
    candles = sorted(out.values(), key=lambda k: k["time"])
    return candles[:-1][-bars:]  # descarta o candle em formação


def _simulate(sig, fv_feats, candles, i, regimes: RegimeSeries, opp_by_bar, max_hold, exit_style, entry_regime,
              bar_ms: int = HOUR):
    """Simula um trade aberto na abertura do candle i+1. Retorna dict ou None se não houver candles."""
    n = len(candles)
    if i + 1 >= n:
        return None
    d = 1 if sig.direction == "LONG" else -1
    entry_raw = candles[i + 1]["open"]
    entry = entry_raw * (1 + d * SLIP)
    stop = entry_raw * (sig.stop / sig.ref_price)
    target = entry_raw * (sig.target / sig.ref_price)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reason, exit_px, j_exit = None, None, None
    for j in range(i + 1, n):
        k = candles[j]
        if j > i + 1 and ((d > 0 and k["open"] <= stop) or (d < 0 and k["open"] >= stop)):
            reason, exit_px, j_exit = "STOP_GAP", k["open"], j
            break
        hit_stop = (k["low"] <= stop) if d > 0 else (k["high"] >= stop)
        hit_tgt = (k["high"] >= target) if d > 0 else (k["low"] <= target)
        if hit_stop:
            reason, exit_px, j_exit = "STOP", stop, j
            break
        if hit_tgt:
            reason, exit_px, j_exit = "TARGET", target, j
            break
        bars = j - i
        close_t = k["time"] + bar_ms
        if bars >= max_hold:
            reason, exit_px, j_exit = "TIME_EXIT", k["close"], j
            break
        reg = regimes.at(close_t)
        if reg == HOSTILE[sig.direction] and reg != entry_regime:
            reason, exit_px, j_exit = "REGIME_CHANGE", k["close"], j
            break
        if sig.direction in opp_by_bar.get(j, set()):
            reason, exit_px, j_exit = "EXIT_SIGNAL", k["close"], j
            break
        if exit_style == "trend" and bars >= 2:
            c, e21, h = fv_feats["close"][j], fv_feats["ema21"][j], fv_feats["macd_hist"][j]
            if (d > 0 and c < e21 and h < 0) or (d < 0 and c > e21 and h > 0):
                reason, exit_px, j_exit = "MOMENTUM_LOSS", c, j
                break
    if reason is None:
        return None  # trade ainda aberto no fim dos dados: não conta
    exit_eff = exit_px * (1 - d * SLIP)
    hours = (j_exit - i) * bar_ms / HOUR
    cost = entry_raw * (2 * FEE) + entry_raw * FUNDING_8H * (hours / 8)
    r = (d * (exit_eff - entry) - cost) / risk
    return {"r": round(r, 4), "exit_reason": reason, "bars": j_exit - i, "exit_index": j_exit,
            "entry_time": candles[i + 1]["time"], "exit_time": candles[j_exit]["time"] + bar_ms}


def run_research(days: int | None = None, top_n: int | None = None, timeframes: list[str] | None = None) -> dict:
    """Uma pesquisa por timeframe, em sequência. `days` só vale quando um único timeframe é pedido."""
    if not _running.acquire(blocking=False):
        return {"skipped": "pesquisa já em execução"}
    try:
        tfs = timeframes or registry.TIMEFRAMES
        out = {}
        for tf in tfs:
            try:
                out[tf] = _run(tf, days if len(tfs) == 1 else None, top_n)
            except Exception as exc:
                out[tf] = {"error": str(exc)}
        return out
    finally:
        _running.release()


def start_background(days: int | None = None, top_n: int | None = None, timeframes: list[str] | None = None) -> bool:
    if _running.locked():
        return False
    threading.Thread(target=run_research, args=(days, top_n, timeframes), daemon=True, name="quant-research").start()
    return True


def kind_for(tf: str) -> str:
    return f"wf_{tf}"


def _progress(run_id: int, text: str) -> None:
    with session_scope() as db:
        db.get(ResearchRun, run_id).progress = text


def _run(tf, days, top_n) -> dict:
    registry.sync_definitions()
    with session_scope() as db:
        qc = db.get(QuantConfig, 1)
        days = days or {"1h": qc.research_days, "15m": qc.research_days_15m or 60, "5m": qc.research_days_5m or 30}[tf]
        top_n = top_n or qc.research_top_n
        weights = dict(qc.weights or {})
        min_final, min_lvl, single_min = qc.min_final_score, qc.min_confluence_level, qc.single_signal_min_score
    cfg = get_config()
    cost = {"fee_pct": cfg.fee_rate_pct, "spread_pct": 2 * SLIP * 100, "ratio": cfg.min_stop_cost_ratio}
    versions = {s.strategy_id: s.version for s in registry.SIGNAL_STRATEGIES if s.timeframe == tf}
    params = {"days": days, "top_n": top_n, "timeframe": tf, "cost_filter": cost, "fee_pct_side": FEE * 100, "slippage_pct_side": SLIP * 100,
              "funding_pct_8h": FUNDING_8H * 100, "folds": FOLDS, "data_source": data.DATA_SOURCE["name"],
              "entry": "abertura do candle seguinte", "stop_gap_modeled": True}
    with session_scope() as db:
        run = ResearchRun(kind=kind_for(tf), params=params, strategy_versions=versions, status="running",
                          progress=f"{tf}: iniciando")
        db.add(run)
        db.flush()
        run_id = run.id
    try:
        results = _execute(run_id, tf, days, top_n, weights, min_final, min_lvl, single_min, cost)
    except Exception as exc:
        with session_scope() as db:
            r = db.get(ResearchRun, run_id)
            r.status, r.error, r.finished_at = "error", str(exc), utcnow()
        events.emit("ERROR", message=f"pesquisa #{run_id} falhou: {exc}")
        raise
    with session_scope() as db:
        r = db.get(ResearchRun, run_id)
        r.status, r.finished_at, r.progress = "done", utcnow(), "concluída"
        r.summary, r.results = results["summary"], results
    events.emit("RESEARCH_FINISHED", message=f"pesquisa #{run_id} ({tf}) concluída", run_id=run_id, timeframe=tf,
                trades=results["summary"]["trades"])
    return {"run_id": run_id, **results["summary"]}


def _execute(run_id, tf, days, top_n, weights, min_final, min_lvl, single_min, cost) -> dict:
    bar_ms = registry.TF_MS[tf]
    bars = int(days * 24 * HOUR / bar_ms) + WARMUP
    bars_1h = days * 24 + WARMUP

    def cost_ok(sig) -> bool:
        if not cost["ratio"]:
            return True
        stop_pct = abs(sig.ref_price - sig.stop) / sig.ref_price * 100
        return stop_pct >= cost["ratio"] * (2 * cost["fee_pct"] + cost["spread_pct"])

    tickers = []
    for t in data.market().get_tickers():
        s = str(t.get("symbol", ""))
        if s.endswith("-USDT") and not NON_CRYPTO.match(s):
            last = float(t.get("lastPrice") or 0)
            qv = float(t.get("quoteVolume") or 0) or float(t.get("volume") or 0) * last
            tickers.append((qv, s))
    symbols = [s for _, s in sorted(tickers, reverse=True)[:top_n]]
    if "BTC-USDT" not in symbols:
        symbols.insert(0, "BTC-USDT")
    _progress(run_id, "baixando BTC (regime)")
    btc1h = fetch_history("BTC-USDT", "1h", bars_1h + 50)
    btc4h = fetch_history("BTC-USDT", "4h", bars_1h // 4 + 200)
    regimes = RegimeSeries(btc1h, btc4h)
    strategies = [s for s in registry.SIGNAL_STRATEGIES if s.timeframe == tf]
    with session_scope() as db:
        mom_tfs = db.get(QuantConfig, 1).momentum_exit_timeframes
    def style(strat):   # mesma regra do motor: saída por perda de momentum só nos timeframes configurados
        return strat.exit_style if (mom_tfs is None or tf in mom_tfs) else "no_momentum"

    flt = registry.BY_ID["BTC_REGIME_FILTER"]
    settings = registry.settings_map()

    trades: list[dict] = []
    engine_trades: list[dict] = []
    signal_counts = defaultdict(int)
    t_start = t_end = None
    for n_sym, sym in enumerate(symbols, 1):
        _progress(run_id, f"{tf}: {n_sym}/{len(symbols)} {sym}")
        try:
            candles = btc1h if (sym == "BTC-USDT" and tf == "1h") else fetch_history(sym, tf, bars)
        except Exception as exc:
            botlog.warn(MODULE, f"{sym}: sem histórico ({exc})")
            continue
        if len(candles) < WARMUP + 100:
            continue
        feats = F.compute(candles)
        t_start = min(t_start or candles[WARMUP]["time"], candles[WARMUP]["time"])
        t_end = max(t_end or 0, candles[-1]["time"])
        # sinais por candle
        by_bar: dict[int, list] = {}
        for i in range(WARMUP, len(candles) - 1):
            fv = FeatureView(feats, i, candles)
            reg = regimes.at(candles[i]["time"] + bar_ms)
            ctx = {"btc_regime": reg}
            lst = []
            for s in strategies:
                for sig in s.generate_signal(fv, ctx):
                    lst.append((sig, reg))
                    signal_counts[(s.strategy_id, sig.direction)] += 1
                    signal_counts[(s.strategy_id, "ALL")] += 1
            if lst:
                by_bar[i] = lst
        opp_dirs = {i: {("SHORT" if sg.direction == "LONG" else "LONG") for sg, _ in v} for i, v in by_bar.items()}
        # trades por estratégia isolada
        busy_until: dict[str, int] = {}
        for i in sorted(by_bar):
            for sig, reg in by_bar[i]:
                s = registry.BY_ID[sig.strategy_id]
                if busy_until.get(sig.strategy_id, -1) >= i:
                    continue
                res = _simulate(sig, feats, candles, i, regimes, opp_dirs, s.max_hold_bars, style(s), reg, bar_ms)
                if not res:
                    continue
                busy_until[sig.strategy_id] = res["exit_index"]
                co = sorted({sg.strategy_id for sg, _ in by_bar[i] if sg.direction == sig.direction})
                st = settings.get(sig.strategy_id, {})
                trades.append({**res, "symbol": sym, "direction": sig.direction, "primary": sig.strategy_id,
                               "strategy": sig.strategy_id, "strategies": co if co else [sig.strategy_id],
                               "regime": reg, "score": sig.score, "time": res["exit_time"],
                               "would_pass_filters": reg in (st.get("accepted_regimes") or [])
                               and flt.allows(sig.direction, reg, sym)[0] and sig.score >= st.get("min_score", 0)
                               and cost_ok(sig)})
        # motor completo (filtros atuais + confluência + score; um trade por par por vez)
        busy = -1
        for i in sorted(by_bar):
            if i <= busy:
                continue
            groups = {}
            for sig, reg in by_bar[i]:
                st = settings.get(sig.strategy_id, {})
                if reg not in (st.get("accepted_regimes") or []) or not flt.allows(sig.direction, reg, sym)[0] \
                        or sig.score < st.get("min_score", 0) or not cost_ok(sig):
                    continue
                g = groups.setdefault(sig.direction, ConfluenceGroup(sym, sig.direction, candles[i]["time"]))
                g.signals.append(sig)
            best = None
            for g in groups.values():
                reg = regimes.at(candles[i]["time"] + bar_ms)
                sc = score_group(g, FeatureView(feats, i, candles), reg, weights, None)
                lvl_ok = LEVEL_RANK[g.level] >= LEVEL_RANK[min_lvl] or (g.level == "SINGLE_SIGNAL" and sc["final_score"] >= single_min)
                if sc["final_score"] >= min_final and lvl_ok and (best is None or sc["final_score"] > best[1]["final_score"]):
                    best = (g, sc, reg)
            if len(groups) == 2 and best:
                scores = [score_group(g, FeatureView(feats, i, candles), best[2], weights, None)["final_score"] for g in groups.values()]
                if abs(scores[0] - scores[1]) < 15:
                    continue
            if not best:
                continue
            g, sc, reg = best
            p = g.primary
            s = registry.BY_ID[p.strategy_id]
            res = _simulate(p, feats, candles, i, regimes, opp_dirs, s.max_hold_bars, style(s), reg, bar_ms)
            if not res:
                continue
            busy = res["exit_index"]
            engine_trades.append({**res, "symbol": sym, "direction": g.direction, "primary": p.strategy_id,
                                  "strategies": g.strategies, "level": g.level, "regime": reg,
                                  "final_score": sc["final_score"], "time": res["exit_time"]})

    trades.sort(key=lambda t: t["exit_time"])
    engine_trades.sort(key=lambda t: t["exit_time"])
    span = (t_end or 0) - (t_start or 0)
    fold_edges = [t_start + span * k / FOLDS for k in range(FOLDS + 1)] if t_start else []
    recent_cut = (t_end or 0) - min(30 * 24 * HOUR, span / 3)   # RECENT = último terço (máx. 30 dias)

    def fold_of(t):
        for k in range(FOLDS):
            if t["entry_time"] < fold_edges[k + 1] or k == FOLDS - 1:
                return k
        return FOLDS - 1

    per_strategy = {}
    wf = {}
    for s in strategies:
        sid = s.strategy_id
        mine = [t for t in trades if t["primary"] == sid]
        folds = [performance.metrics([t for t in mine if fold_of(t) == k]) for k in range(FOLDS)] if fold_edges else []
        valid = [f for f in folds if f["trades"] >= 5]
        pos = sum(1 for f in valid if f["expectancy"] > 0)
        wf[sid] = {"folds": folds, "positive_fraction": round(pos / len(valid), 2) if valid else None}
        per_strategy[sid] = {
            "all": performance.metrics(mine),
            "LONG": performance.metrics([t for t in mine if t["direction"] == "LONG"]),
            "SHORT": performance.metrics([t for t in mine if t["direction"] == "SHORT"]),
            "with_current_filters": performance.metrics([t for t in mine if t["would_pass_filters"]]),
            "exit_reasons": dict(_count(t["exit_reason"] for t in mine)),
            "by_regime": {r: performance.metrics([t for t in mine if t["regime"] == r])
                          for r in sorted({t["regime"] for t in mine})},
            "walk_forward": wf[sid],
        }
    # seleção ancorada fora da amostra
    selection = []
    for k in range(1, FOLDS):
        chosen = []
        for s in strategies:
            prior = [t for t in trades if t["primary"] == s.strategy_id and fold_of(t) < k]
            m = performance.metrics(prior)
            if m["trades"] >= 10 and m["expectancy"] > 0:
                chosen.append(s.strategy_id)
        oos = [t for t in trades if t["primary"] in chosen and fold_of(t) == k]
        selection.append({"fold": k + 1, "selected": chosen, "oos": performance.metrics(oos)})

    combos = defaultdict(list)
    for t in engine_trades:
        if len(t["strategies"]) >= 2:
            combos["+".join(t["strategies"])].append(t)
    engine = {
        "all": performance.metrics(engine_trades),
        "LONG": performance.metrics([t for t in engine_trades if t["direction"] == "LONG"]),
        "SHORT": performance.metrics([t for t in engine_trades if t["direction"] == "SHORT"]),
        "by_level": {lv: performance.metrics([t for t in engine_trades if t["level"] == lv])
                     for lv in ("SINGLE_SIGNAL", "CONFLUENCE", "STRONG_CONFLUENCE")},
        "by_regime": {r: performance.metrics([t for t in engine_trades if t["regime"] == r])
                      for r in sorted({t["regime"] for t in engine_trades})},
        "folds": [performance.metrics([t for t in engine_trades if fold_of(t) == k]) for k in range(FOLDS)] if fold_edges else [],
        "combinations": {c: performance.metrics(v) for c, v in sorted(combos.items(), key=lambda x: -len(x[1]))[:25]},
        "top_days_dependency": _top_days(engine_trades),
    }
    performance.write_research(trades, [t for t in trades if t["entry_time"] >= recent_cut], signal_counts,
                               engine_trades, tf)
    tags = performance.update_discovery(wf)
    performance.update_health()
    summary = {"timeframe": tf, "symbols": len(symbols), "trades": len(trades), "engine_trades": len(engine_trades),
               "period": {"start": t_start, "end": t_end}, "engine": engine["all"],
               "discovery": tags, "selection_walk_forward": selection}
    return {"summary": summary, "per_strategy": per_strategy, "engine": engine, "selection_walk_forward": selection,
            "symbols": symbols, "fold_edges": fold_edges, "discovery": tags,
            "caveats": ["Universo = top por volume de hoje (viés de sobrevivência/seleção).",
                        "Fills simulados: entrada na abertura seguinte, custos fixos; a DEMO real pode diferir.",
                        "Resultados passados não garantem resultados futuros; nenhuma estratégia é garantida."]}


def _count(it):
    out = defaultdict(int)
    for x in it:
        out[x] += 1
    return out


def _top_days(trades: list[dict]) -> dict:
    by_day = defaultdict(float)
    for t in trades:
        by_day[t["exit_time"] // (24 * HOUR)] += t["r"]
    total = sum(by_day.values())
    best = sorted(by_day.values(), reverse=True)
    return {"total_r": round(total, 2), "days": len(by_day),
            "without_top5_days_r": round(total - sum(best[:5]), 2),
            "without_top10_days_r": round(total - sum(best[:10]), 2)}


def latest_run(tf: str | None = None) -> dict | None:
    """Última pesquisa (de um timeframe, ou de qualquer um)."""
    with session_scope() as db:
        q = select(ResearchRun).order_by(ResearchRun.id.desc()).limit(1)
        if tf:
            kinds = [kind_for(tf)] + (["backtest_walkforward"] if tf == "1h" else [])
            q = q.where(ResearchRun.kind.in_(kinds))
        r = db.scalar(q)
        if not r:
            return None
        return {"id": r.id, "timeframe": (r.params or {}).get("timeframe", "1h"), "status": r.status, "created_at": r.created_at.isoformat(),
                "finished_at": r.finished_at and r.finished_at.isoformat(), "progress": r.progress,
                "params": r.params, "summary": r.summary, "results": r.results, "error": r.error,
                "strategy_versions": r.strategy_versions}
