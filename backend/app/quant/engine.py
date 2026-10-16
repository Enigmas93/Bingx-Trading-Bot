"""Motor quantitativo multi-estratégia (DEMO ONLY).

Roda em 3 timeframes (1h, 15m, 5m): cada estratégia tem uma variante por timeframe, com configuração e desempenho
próprios. O job roda a cada 5 min e processa cada candle fechado de cada timeframe uma única vez. O regime do BTC é
sempre o do 1h (contexto de mercado).
Pipeline por candle fechado:
  dados da conta REAL → regime do BTC → features por par → sinais de cada estratégia (LONG e SHORT separados)
  → filtros por estratégia (direção, regime, score, cooldown, limite diário) → confluência → score → ranking
  → risco/segurança DEMO → execução DEMO → motor de saídas → diário.
Nenhuma informação de análise vem da conta demo; a demo só recebe as ordens (ver data.py).
"""

import math
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import botlog
from ..bingx import BingXError
from ..common import get_config
from ..database import session_scope
from ..models import utcnow
from ..scanner import NON_CRYPTO
from . import data, events, registry
from . import features as F
from .confluence import build_groups
from .exits import evaluate_exit
from .features import FeatureView
from .models import EngineCycle, MarketRegime, Opportunity, StrategyPerformance, StrategySignal, TradeJournal
from .monitor import open_trades
from .ranking import rank_and_decide
from .regime import REGIME_LABELS, classify_live
from .safety import check_demo_safety
from .scoring import score_group

MODULE = "quant"
CANDLES = 300
WORKERS = 4
HOUR_MS = 3_600_000
_lock = threading.Lock()
last_cycle: dict = {}   # timeframe -> resumo do último ciclo


# ---------------------------------------------------------------- helpers
def _symbol_state(fv) -> str:
    c, e21, e50, adx = fv["close"], fv["ema21"], fv["ema50"], fv["adx"]
    if any(math.isnan(x) for x in (c, e21, e50, adx)):
        return "INDEFINIDO"
    if c > e21 > e50:
        trend = "ALTA"
    elif c < e21 < e50:
        trend = "BAIXA"
    else:
        trend = "LATERAL"
    force = "forte" if adx >= 25 else ("moderada" if adx >= 20 else "fraca")
    return f"{trend} · ADX {adx:.0f} ({force})"


def _hist_stats() -> dict:
    """Expectativa histórica (backtest) por estratégia/direção, atribuição primária."""
    out = {}
    with session_scope() as db:
        for r in db.scalars(select(StrategyPerformance).where(StrategyPerformance.scope == "HISTORICAL",
                                                              StrategyPerformance.attribution == "primary")):
            out[(r.strategy_name, r.direction)] = {"expectancy": r.expectancy, "trades": r.executions}
    return out


def blocked_hours(qc, now=None) -> str | None:
    """Motivo do bloqueio por horário (no fuso configurado) ou None."""
    if not qc or not qc.block_hours_enabled:
        return None
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(qc.block_timezone or "America/Sao_Paulo")
    except Exception:
        from datetime import timezone as _tz
        tz = _tz(timedelta(hours=-3))
    local = (now or utcnow()).astimezone(tz)
    s, e, h = qc.block_start_hour, qc.block_end_hour, local.hour
    inside = (s <= h < e) if s < e else (h >= s or h < e)
    if inside:
        return f"horário bloqueado para novas entradas ({s:02d}:00–{e:02d}:00, horário local; agora {local:%H:%M})"
    return None


def _risk_pause(cfg, equity: float | None, reset_at=None) -> str | None:
    day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    if reset_at is not None:
        reset_at = reset_at if reset_at.tzinfo else reset_at.replace(tzinfo=day.tzinfo)
        day = max(day, reset_at)
    with session_scope() as db:
        rows = db.scalars(select(TradeJournal).where(TradeJournal.status == "CLOSED", TradeJournal.exit_time >= day)
                          .order_by(TradeJournal.exit_time.desc())).all()
        pnls = [r.pnl or 0.0 for r in rows]
    daily = sum(pnls)
    if equity and cfg.daily_loss_limit_pct > 0 and daily <= -(equity - daily) * cfg.daily_loss_limit_pct / 100:
        return f"limite de perda diária DEMO atingido ({daily:.2f})"
    streak = 0
    for p in pnls:
        if p < 0:
            streak += 1
        else:
            break
    if cfg.max_consecutive_losses > 0 and streak >= cfg.max_consecutive_losses:
        return f"{streak} perdas seguidas hoje (limite {cfg.max_consecutive_losses})"
    return None


def _strategy_usage() -> tuple[Counter, dict]:
    """Trades de hoje por estratégia primária e último trade por (estratégia, par)."""
    day = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today, last = Counter(), {}
    with session_scope() as db:
        for r in db.scalars(select(TradeJournal).where(TradeJournal.status != "FAILED",
                                                       TradeJournal.created_at >= day - timedelta(days=1))):
            created = r.created_at if r.created_at.tzinfo else r.created_at.replace(tzinfo=day.tzinfo)
            if created >= day:
                today[r.strategy_primary] += 1
            k = (r.strategy_primary, r.symbol)
            last[k] = max(last.get(k, created), created)
    return today, last


def _universe(cfg) -> list[dict]:
    mk = data.market()
    tradable = set(data.execution().get_contracts())   # pares que existem na conta de execução
    tickers = []
    for t in mk.get_tickers():
        s = str(t.get("symbol", ""))
        if not s.endswith("-USDT") or NON_CRYPTO.match(s):
            continue
        last = float(t.get("lastPrice") or 0)
        qv = float(t.get("quoteVolume") or 0) or float(t.get("volume") or 0) * last
        if last > 0 and qv >= cfg.min_volume_usdt:
            tickers.append({"symbol": s, "quote_volume": qv})
    tickers.sort(key=lambda x: x["quote_volume"], reverse=True)
    return [t for t in tickers[: cfg.scan_top_n] if t["symbol"] in tradable]


def _corr_fn(closes: dict):
    cache = {}

    def corr(a: str, b: str):
        key = tuple(sorted((a, b)))
        if key in cache:
            return cache[key]
        ca, cb = closes.get(a), closes.get(b)
        rho = None
        if ca is not None and cb is not None:
            n = min(len(ca), len(cb), 101)
            if n > 30:
                ra, rb = np.diff(np.log(ca[-n:])), np.diff(np.log(cb[-n:]))
                if ra.std() > 0 and rb.std() > 0:
                    rho = float(np.corrcoef(ra, rb)[0, 1])
        cache[key] = rho
        return rho
    return corr


# ---------------------------------------------------------------- ciclo
def active_timeframes(qc) -> list[str]:
    tfs = qc.timeframes if qc and qc.timeframes else registry.TIMEFRAMES
    return [tf for tf in registry.TIMEFRAMES if tf in tfs]


def run_engine(force: bool = False, timeframes: list[str] | None = None) -> dict:
    """Processa, em ordem 1h → 15m → 5m, cada timeframe cujo candle fechado ainda não foi processado."""
    if not _lock.acquire(blocking=False):
        return {"skipped": "ciclo quant já em execução"}
    try:
        registry.sync_definitions()
        tfs = timeframes or active_timeframes(registry.quant_config())
        out = {}
        for tf in tfs:
            try:
                out[tf] = _cycle(tf, force)
            except Exception as exc:
                events.emit("ERROR", message=f"falha no ciclo quant {tf}: {exc}", timeframe=tf)
                out[tf] = {"error": str(exc)}
        return out
    finally:
        _lock.release()


def _regime() -> dict:
    """Regime do BTC no último candle 1h fechado (conta real). Registra cada candle 1h uma vez."""
    btc1h = data.closed_klines("BTC-USDT", "1h", CANDLES)
    btc4h = data.closed_klines("BTC-USDT", "4h", 200)
    t1h = btc1h[-1]["time"]
    reg = classify_live(btc1h, btc4h)
    with session_scope() as db:
        done = db.scalar(select(MarketRegime).where(MarketRegime.candle_time == t1h))
        prev = db.scalar(select(MarketRegime).order_by(MarketRegime.candle_time.desc()).limit(1))
        prev_regime = prev.btc_regime if prev else None
        if not done:
            db.add(MarketRegime(candle_time=t1h, btc_regime=reg["btc_regime"], market_state=reg["market_state"],
                                previous_regime=prev_regime,
                                changed=bool(prev_regime and prev_regime != reg["btc_regime"]), details=reg))
    if not done and prev_regime and prev_regime != reg["btc_regime"]:
        events.emit("REGIME_CHANGED", symbol="BTC-USDT", previous=prev_regime, current=reg["btc_regime"],
                    message=f"{REGIME_LABELS.get(prev_regime, prev_regime)} → {REGIME_LABELS[reg['btc_regime']]}")
    return reg


def _cycle(tf: str, force: bool) -> dict:
    qc = registry.quant_config()
    cfg = get_config()
    safety = check_demo_safety()
    tf_ms = registry.TF_MS[tf]

    # candle fechado deste timeframe (BTC como relógio de referência)
    candle_time = data.closed_klines("BTC-USDT", tf, 3)[-1]["time"]
    with session_scope() as db:
        done = db.scalar(select(EngineCycle).where(EngineCycle.timeframe == tf, EngineCycle.candle_time == candle_time))
    if done and not force:
        return {"skipped": f"candle {tf} já processado", "candle_time": candle_time}

    # 1) regime do BTC (sempre 1h, conta real)
    reg = _regime()
    btc_regime = reg["btc_regime"]

    # 2) universo (ranking por volume da conta real) + pares com trade aberto
    universe = _universe(cfg)
    trades_open = open_trades()
    trades_tf = [t for t in trades_open if t["timeframe"] == tf]
    symbols = list(dict.fromkeys([u["symbol"] for u in universe] + [t["symbol"] for t in trades_tf]))
    settings = registry.settings_map()
    active = [s for s in registry.SIGNAL_STRATEGIES if s.timeframe == tf
              and settings.get(s.strategy_id, {}).get("enabled") and not registry.blocked_reason(s.strategy_id)]
    flt = registry.BY_ID["BTC_REGIME_FILTER"]
    flt_on = settings.get("BTC_REGIME_FILTER", {}).get("enabled", True)
    ctx = {"btc_regime": btc_regime}

    # 3) features + sinais por par (paralelo)
    def analyze(sym: str):
        candles = data.closed_klines(sym, tf, CANDLES)
        if len(candles) < 150 or candles[-1]["time"] != candle_time:
            return sym, None, [], "dados insuficientes ou candle desatualizado"
        feats = F.compute(candles)
        fv = FeatureView(feats, len(candles) - 1, candles)
        sigs = []
        for s in active:
            try:
                sigs += s.generate_signal(fv, ctx)
            except Exception as exc:  # uma estratégia com erro não derruba as outras
                botlog.warn(MODULE, f"{s.strategy_id} em {sym}: {exc}")
        return sym, fv, sigs, None

    def safe(sym):
        try:
            return analyze(sym)
        except Exception as exc:
            return sym, None, [], str(exc)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(safe, symbols))

    fvs, closes, errors = {}, {}, {}
    raw: dict[str, list] = {}
    for sym, fv, sigs, err in results:
        if err:
            errors[sym] = err
            continue
        fvs[sym] = fv
        closes[sym] = np.asarray(fv.f["close"][-101:], dtype=float)
        raw[sym] = sigs

    # 4) filtros por estratégia + gravação dos sinais (um por estratégia/par/direção/candle)
    today, last_trade = _strategy_usage()
    now = utcnow()
    accepted: dict[str, list] = {}
    sig_rows: dict[tuple, int] = {}
    counts = Counter()
    for sym, sigs in raw.items():
        for s in sigs:
            st = settings[s.strategy_id]
            status, why = "ACCEPTED", None
            if (s.direction == "LONG" and not st["allow_long"]) or (s.direction == "SHORT" and not st["allow_short"]):
                status, why = "DIRECTION_DISABLED", f"{s.direction} desativado para a estratégia"
            elif btc_regime not in (st["accepted_regimes"] or []):
                status, why = "REGIME_BLOCKED", f"regime {btc_regime} não aceito pela estratégia"
            elif flt_on and not flt.allows(s.direction, btc_regime, sym)[0]:
                status, why = "REGIME_BLOCKED", flt.allows(s.direction, btc_regime, sym)[1]
            elif s.score < st["min_score"]:
                status, why = "BELOW_MIN", f"score {s.score:.0f} < mínimo {st['min_score']:.0f}"
            elif today[s.strategy_id] >= st["max_trades_per_day"]:
                status, why = "DAILY_LIMIT", f"limite de {st['max_trades_per_day']} trades/dia da estratégia"
            else:
                lt = last_trade.get((s.strategy_id, sym))
                if lt and now - lt < timedelta(minutes=st["cooldown_minutes"]):
                    status, why = "COOLDOWN", f"cooldown de {st['cooldown_minutes']} min no par"
            row = StrategySignal(
                candle_time=candle_time, strategy_id=s.strategy_id, strategy_version=s.version, symbol=sym,
                direction=s.direction, timeframe=s.timeframe, score=s.score, confidence=s.confidence,
                reason=s.reason + (f" | BLOQUEIO: {why}" if why else ""),
                conditions=[[t, bool(ok)] for t, ok in s.conditions], regime=btc_regime, ref_price=s.ref_price,
                stop=s.stop, target=s.target, expected_r=round(s.reward_r, 3), features=s.features_used,
                status=status)
            try:
                with session_scope() as db:
                    db.add(row)
                    db.flush()
                    sig_rows[(s.strategy_id, sym, s.direction)] = row.id
            except IntegrityError:
                counts["DUPLICATE"] += 1      # já registrado neste candle (ciclo repetido)
                continue
            counts[status] += 1
            events.emit("SIGNAL_GENERATED", symbol=sym, strategy=s.strategy_id, log=False, direction=s.direction,
                        score=s.score, status=status, why=why)
            if status == "ACCEPTED":
                accepted.setdefault(sym, []).append(s)

    # 5) confluência + score
    hist = _hist_stats()
    opps = []
    groups = {}
    for sym, sigs in accepted.items():
        for g in build_groups(sym, candle_time, sigs):
            sc = score_group(g, fvs[sym], btc_regime, qc.weights,
                             hist.get((g.primary.strategy_id, g.direction)))
            p = g.primary
            groups[(sym, g.direction)] = g
            opps.append({"symbol": sym, "direction": g.direction, "confluence_level": g.level,
                         "primary_strategy": p.strategy_id, "reward_r": p.reward_r, "group": g, **sc})

    # 5b) custos reais (conta real): spread/funding e filtro de custo — o stop precisa valer N× o custo da operação
    for o in opps:
        p = o["group"].primary
        try:
            bid, ask = data.market().get_book_top(o["symbol"])
            spread = (ask - bid) / ((ask + bid) / 2) * 100
            funding = data.market().get_funding_rate(o["symbol"]) * 100
        except BingXError as exc:
            o["pre_reasons"] = [f"sem book/funding: {exc}"]
            continue
        o["spread_pct"], o["funding_pct"] = round(spread, 4), round(funding, 5)
        stop_pct = abs(p.ref_price - p.stop) / p.ref_price * 100
        cost_pct = 2 * cfg.fee_rate_pct + spread
        o["stop_pct"], o["cost_pct"] = round(stop_pct, 4), round(cost_pct, 4)
        if spread > cfg.max_spread_pct:
            o["pre_reasons"] = [f"spread {spread:.3f}% > máximo {cfg.max_spread_pct}%"]
        elif abs(funding) > cfg.max_funding_pct:
            o["pre_reasons"] = [f"funding {funding:.4f}% > máximo {cfg.max_funding_pct}%"]
        elif cfg.min_stop_cost_ratio > 0 and stop_pct < cfg.min_stop_cost_ratio * cost_pct:
            o["pre_reasons"] = [f"custo alto: stop de {stop_pct:.2f}% < {cfg.min_stop_cost_ratio:g}× o custo da "
                                f"operação ({cost_pct:.2f}% = taxas de ida e volta + spread)"]

    # 6) ranking + decisão
    adapter_client = data.execution()
    open_list, demo_positions = [], []
    try:
        demo_positions = adapter_client.get_positions()
        equity = adapter_client.get_balance()["equity"]
    except BingXError as exc:
        botlog.warn(MODULE, f"DEMO indisponível para posições/saldo: {exc}")
        equity = None
    open_list = [(p["symbol"], p["direction"]) for p in demo_positions] + \
                [(t["symbol"], t["direction"]) for t in trades_open]
    open_list = list(dict.fromkeys(open_list))
    rctx = {
        "min_final_score": qc.min_final_score, "min_confluence_level": qc.min_confluence_level,
        "single_signal_min_score": qc.single_signal_min_score, "conflict_margin": qc.conflict_margin,
        "max_positions": cfg.max_positions, "max_same_direction": cfg.max_same_direction,
        "max_open_per_strategy": qc.max_open_per_strategy, "max_correlation": qc.max_correlation,
        "open_symbols": {s for s, _ in open_list}, "open_count": len(open_list),
        "open_dirs": Counter(d for _, d in open_list),
        "open_per_strategy": Counter(t["strategy_primary"] for t in trades_open),
        "risk_paused": blocked_hours(qc) or _risk_pause(cfg, equity, qc.risk_reset_at), "correlation": _corr_fn(closes), "open_list": open_list,
        "engine_ready": safety["safety_ok"] and qc.engine_enabled,
        "engine_reason": ("; ".join(safety["reasons"]) if not safety["safety_ok"]
                          else "execução automática desligada (modo observação)"),
    }
    ranked = rank_and_decide(opps, rctx)

    opp_ids = {}
    for o in ranked:
        g = o["group"]
        with session_scope() as db:
            existing = db.scalar(select(Opportunity).where(Opportunity.symbol == o["symbol"],
                                                           Opportunity.direction == o["direction"],
                                                           Opportunity.timeframe == tf,
                                                           Opportunity.candle_time == candle_time))
            if existing:
                continue
            row = Opportunity(
                candle_time=candle_time, symbol=o["symbol"], direction=o["direction"], timeframe=tf,
                confluence_level=g.level, confluence_group_id=g.group_id, primary_strategy=g.primary.strategy_id,
                supporting_strategies=[s.strategy_id for s in g.supporting],
                strategy_scores={s.strategy_id: s.score for s in g.signals}, btc_regime=btc_regime,
                base_score=o["base_score"], regime_score=o["regime_score"], confluence_score=o["confluence_score"],
                final_score=o["final_score"], score_components={**o["components"], "weights": o["weights"]},
                rank=o["rank"], status="RANKED" if o["status"] == "SELECTED" else o["status"],
                decision_reasons=o["reasons"])
            db.add(row)
            db.flush()
            opp_ids[(o["symbol"], o["direction"])] = row.id
            for s in g.signals:
                sid = sig_rows.get((s.strategy_id, o["symbol"], o["direction"]))
                if sid:
                    sr = db.get(StrategySignal, sid)
                    sr.opportunity_id = row.id
        if o["rank"] <= 10:
            events.emit("OPPORTUNITY_RANKED", symbol=o["symbol"], strategy=g.primary.strategy_id, log=o["rank"] <= 3,
                        rank=o["rank"], direction=o["direction"], final_score=o["final_score"],
                        confluence=g.level, status=o["status"], reasons=o["reasons"],
                        message=f"#{o['rank']} {o['direction']} score {o['final_score']} ({g.level}) → {o['status']}")

    # 7) execução DEMO
    executed = []
    selected = [o for o in ranked if o["status"] == "SELECTED"]
    if selected and rctx["engine_ready"]:
        from .execution import get_adapter
        from ..news import check_news_before_trade
        adapter = get_adapter()
        for o in selected:
            oid = opp_ids.get((o["symbol"], o["direction"]))
            if oid is None:
                continue
            news = None
            if cfg.news_check_enabled:
                news = check_news_before_trade(o["symbol"], o["direction"], cfg.news_block_generic)
                if not news.get("allow", True):
                    _set_opp(oid, "SKIPPED", f"notícia contra a operação: {news.get('summary')}")
                    continue
            jf, snap = _journal_fields(o, fvs[o["symbol"]], reg, oid, news, spread=o.get("spread_pct"),
                                       funding=o.get("funding_pct"))
            g = o["group"]
            res = adapter.open_trade({"symbol": o["symbol"], "direction": o["direction"], "ref_price": g.primary.ref_price,
                                      "stop": g.primary.stop, "target": g.primary.target, "timeframe": tf}, jf, snap)
            if res.get("success"):
                _set_opp(oid, "EXECUTED", f"executada na DEMO (trade #{res['trade_id']})", trade_id=res["trade_id"])
                _mark_signals_used(oid)
                executed.append(o["symbol"])
            else:
                _set_opp(oid, "FAILED", res.get("error", "falha"), trade_id=res.get("trade_id"))

    # 8) saídas
    exits = _run_exits(trades_tf, fvs, raw, settings, btc_regime, qc, candle_time + tf_ms) if safety["safety_ok"] else []

    # 9) expira sinais/oportunidades de candles anteriores
    with session_scope() as db:
        for r in db.scalars(select(StrategySignal).where(StrategySignal.candle_time < candle_time,
                                                         StrategySignal.timeframe == tf,
                                                         StrategySignal.status == "ACCEPTED")):
            r.status = "EXPIRED"
        for r in db.scalars(select(Opportunity).where(Opportunity.candle_time < candle_time,
                                                      Opportunity.timeframe == tf,
                                                      Opportunity.status == "RANKED")):
            r.status = "EXPIRED"

    summary = {"timeframe": tf, "candle_time": candle_time, "btc_regime": btc_regime, "universe": len(universe),
               "analyzed": len(fvs), "errors": len(errors), "signals": dict(counts),
               "opportunities": len(ranked), "selected": len(selected), "executed": executed, "exits": exits,
               "engine_ready": rctx["engine_ready"], "risk_paused": rctx["risk_paused"]}
    last_cycle[tf] = {**summary, "finished_at": utcnow().isoformat()}
    with session_scope() as db:  # registra o candle como processado e guarda o resumo (sobrevive a reinícios)
        row = db.scalar(select(EngineCycle).where(EngineCycle.timeframe == tf, EngineCycle.candle_time == candle_time))
        if row is None:
            db.add(EngineCycle(timeframe=tf, candle_time=candle_time, summary=events._clean(last_cycle[tf])))
        else:
            row.summary = events._clean(last_cycle[tf])
    botlog.info(MODULE, f"ciclo {tf} {btc_regime}: {len(fvs)} pares, sinais {dict(counts)}, "
                        f"{len(ranked)} oportunidades, {len(selected)} selecionadas, executadas {executed}, saídas {exits}")
    return summary


def _set_opp(oid: int, status: str, reason: str, trade_id: int | None = None) -> None:
    with session_scope() as db:
        r = db.get(Opportunity, oid)
        r.status = status
        r.decision_reasons = list(r.decision_reasons or []) + [reason]
        if trade_id:
            r.trade_id = trade_id


def _mark_signals_used(oid: int) -> None:
    with session_scope() as db:
        for r in db.scalars(select(StrategySignal).where(StrategySignal.opportunity_id == oid)):
            r.status = "USED"


def _journal_fields(o: dict, fv, reg: dict, oid: int, news: dict | None, spread=None, funding=None):
    g = o["group"]
    p = g.primary
    strat = registry.BY_ID[p.strategy_id]
    sym_state = _symbol_state(fv)
    risk_atr = abs(p.ref_price - p.stop) / fv["atr"] if fv["atr"] else None
    why = [f"{s.strategy_id} ({s.score:.0f}): {s.reason}" for s in g.signals]
    expl = {
        "summary": (f"{o['direction']} em {o['symbol']} porque {len(g.signals)} estratégia(s) concordaram "
                    f"({g.level}); primária {strat.name}. Score final {o['final_score']:.1f}/100 no regime "
                    f"{REGIME_LABELS[reg['btc_regime']]}."),
        "strategies": [{"strategy_id": s.strategy_id, "version": s.version, "score": s.score,
                        "role": "primária" if s is p else "apoio", "reason": s.reason,
                        "conditions": [[t, bool(ok)] for t, ok in s.conditions]} for s in g.signals],
        "why": why,
        "score_breakdown": o["components"], "weights": o["weights"],
        "risk": (f"stop {p.stop:.6g} ({risk_atr:.2f} ATR) · alvo {p.target:.6g} ({p.reward_r:.2f}R) · "
                 f"saída máxima em {strat.max_hold_bars} candles" if risk_atr else None),
        "market": {"btc_regime": reg["btc_regime"], "btc_label": REGIME_LABELS[reg["btc_regime"]],
                   "market_state": reg["market_state"], "symbol_state": sym_state,
                   "spread_pct": spread, "funding_pct": funding},
        "decision": o["reasons"],
        "news": news and {k: news.get(k) for k in ("impact", "summary", "news_count")},
        "data_source": data.DATA_SOURCE["name"], "execution": "DEMO (VST)",
    }
    jf = {
        "strategy_primary": p.strategy_id, "strategy_primary_version": p.version,
        "strategies_supporting": [s.strategy_id for s in g.supporting],
        "strategy_versions": {s.strategy_id: s.version for s in g.signals},
        "strategy_scores": {s.strategy_id: s.score for s in g.signals},
        "confluence_level": g.level, "confluence_group_id": g.group_id,
        "base_score": o["base_score"], "regime_score": o["regime_score"], "confluence_score": o["confluence_score"],
        "final_score": o["final_score"], "score_components": o["components"],
        "market_regime": sym_state, "btc_regime": reg["btc_regime"], "market_state": reg["market_state"],
        "entry_reason": " | ".join(why), "explanation": expl, "opportunity_id": oid,
    }
    snap = {
        "features": fv.snapshot(),
        "signals": [{"strategy_id": s.strategy_id, "version": s.version, "direction": s.direction, "score": s.score,
                     "confidence": s.confidence, "ref_price": s.ref_price, "stop": s.stop, "target": s.target,
                     "features_used": s.features_used} for s in g.signals],
        "regime": reg,
        "opportunity": {k: o[k] for k in ("final_score", "base_score", "regime_score", "confluence_score",
                                          "components", "weights", "rank", "reasons")},
    }
    return jf, snap


def _run_exits(trades_open, fvs, raw, settings, btc_regime, qc, now_ms) -> list:
    if not trades_open:
        return []
    from .execution import get_adapter
    adapter = None
    out = []
    for t in trades_open:
        strat = registry.BY_ID.get(t["strategy_primary"])
        opp_dir = "SHORT" if t["direction"] == "LONG" else "LONG"
        opposite = [s.strategy_id for s in raw.get(t["symbol"], []) if s.direction == opp_dir
                    and settings.get(s.strategy_id, {}).get("enabled")
                    and s.score >= settings[s.strategy_id]["min_score"]]
        res = evaluate_exit(t, fvs.get(t["symbol"]), btc_regime, opposite, qc,
                            strat.max_hold_bars if strat else 48, strat.exit_style if strat else "trend", now_ms,
                            registry.TF_MS.get(t["timeframe"], HOUR_MS))
        if not res:
            continue
        adapter = adapter or get_adapter()
        r = adapter.close_trade(t["id"], res[0], res[1])
        out.append({"trade_id": t["id"], "symbol": t["symbol"], "reason": res[0], "ok": r.get("success")})
    return out


def counts_by_status(candle_time: int) -> dict:
    with session_scope() as db:
        rows = db.execute(select(StrategySignal.status, func.count()).where(StrategySignal.candle_time == candle_time)
                          .group_by(StrategySignal.status)).all()
    return {s: n for s, n in rows}
