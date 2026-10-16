"""Desempenho por estratégia, direção, regime e combinação; saúde e classificação de descoberta.

Escopos (nunca misturados):
  HISTORICAL  backtest do período completo da última pesquisa (dados da conta real, custos simulados)
  RECENT      backtest restrito aos últimos 30 dias da mesma pesquisa
  DEMO        trades DEMO reais do diário (PnL realizado na conta demo)
Atribuição:
  participant  o trade conta para TODAS as estratégias que deram sinal (primária + apoio)
  primary      o trade conta só para a estratégia primária
Nada aqui altera parâmetros de estratégia. Com AUTO_DISABLE=false (padrão), a saúde só informa.
"""

from collections import defaultdict
from itertools import combinations

from sqlalchemy import delete, select

from ..database import session_scope
from . import events, registry
from .models import (QuantConfig, StrategyCombination, StrategyPerformance, StrategySettings, StrategySignal,
                     TradeJournal)

DISCOVERY_HELP = {
    "BEST": "Critérios de robustez atendidos: ≥30 trades históricos, expectativa ≥ +0,10R, PF ≥ 1,3 e positiva em ≥ 60% das janelas do walk-forward. Não é garantia de lucro.",
    "PROMISING": "Expectativa histórica positiva, mas sem amostra ou consistência suficientes para ser considerada forte.",
    "WEAK": "Expectativa histórica ≤ 0 com amostra relevante (≥ 30 trades).",
    "REGIME_DEPENDENT": "Resultado depende fortemente do regime do BTC (positiva em um regime, negativa em outros).",
    "LONG_ONLY_STRONG": "LONG com expectativa ≥ +0,10R e SHORT ≤ 0.",
    "SHORT_ONLY_STRONG": "SHORT com expectativa ≥ +0,10R e LONG ≤ 0.",
    "UNVALIDATED": "Sem dados históricos suficientes (rode a pesquisa).",
}
HEALTH_HELP = {
    "ACTIVE": "operando normalmente",
    "WATCH": "resultado recente fraco: em observação",
    "DEGRADED": "expectativa negativa consistente com amostra suficiente",
    "DISABLED": "desativada (pelo usuário ou pela auto-desativação, se ligada)",
}


# ---------------------------------------------------------------- métricas
def metrics(trades: list[dict]) -> dict:
    """trades: [{r, pnl?, duration?, direction, time?}] em ordem cronológica."""
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gp, gl = sum(wins), -sum(losses)
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    pnls = [t.get("pnl") for t in trades if t.get("pnl") is not None]
    durs = [t.get("duration") for t in trades if t.get("duration") is not None]
    last = rs[-10:]
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "win_rate": round(len(wins) / n * 100, 1),
        "profit_factor": round(gp / gl, 2) if gl > 0 else (None if gp == 0 else 99.0),
        "expectancy": round(sum(rs) / n, 3), "total_r": round(sum(rs), 2), "average_r": round(sum(rs) / n, 3),
        "max_drawdown": round(dd, 2), "best": round(max(rs), 2), "worst": round(min(rs), 2),
        "total_pnl": round(sum(pnls), 4) if pnls else None,
        "average_pnl": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "average_duration": round(sum(durs) / len(durs), 1) if durs else None,
        "long_trades": sum(1 for t in trades if t["direction"] == "LONG"),
        "short_trades": sum(1 for t in trades if t["direction"] == "SHORT"),
        "last10": f"{sum(1 for r in last if r > 0)}W/{sum(1 for r in last if r <= 0)}L",
    }


def _demo_trades() -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(TradeJournal).where(TradeJournal.status == "CLOSED", TradeJournal.pnl_r.is_not(None))
                          .order_by(TradeJournal.exit_time)).all()
        return [{"r": r.pnl_r, "pnl": r.pnl, "duration": r.duration_minutes, "direction": r.direction,
                 "primary": r.strategy_primary, "strategies": sorted((r.strategy_scores or {}).keys()),
                 "regime": r.btc_regime, "time": r.exit_time, "versions": r.strategy_versions or {},
                 "timeframe": r.timeframe} for r in rows]


def _write_perf(scope: str, trades: list[dict], signal_counts: dict | None = None, timeframe: str | None = None) -> None:
    """Recalcula as linhas de strategy_performance do escopo (participant e primary × ALL/LONG/SHORT).
    Com `timeframe`, só as variantes daquele timeframe são recalculadas (as outras ficam intactas)."""
    settings = registry.settings_map()
    strategies = [s for s in registry.SIGNAL_STRATEGIES if timeframe is None or s.timeframe == timeframe]
    with session_scope() as db:
        db.execute(delete(StrategyPerformance).where(StrategyPerformance.scope == scope,
                                                     StrategyPerformance.strategy_name.in_([s.strategy_id for s in strategies])))
        for s in strategies:
            sid = s.strategy_id
            for attribution in ("participant", "primary"):
                mine = [t for t in trades if (t["primary"] == sid if attribution == "primary" else sid in t["strategies"])]
                for direction in ("ALL", "LONG", "SHORT"):
                    sel = [t for t in mine if direction == "ALL" or t["direction"] == direction]
                    m = metrics(sel)
                    by_regime = defaultdict(list)
                    for t in sel:
                        by_regime[t.get("regime") or "?"].append(t)
                    db.add(StrategyPerformance(
                        strategy_name=sid, version=settings.get(sid, {}).get("active_version", s.version), scope=scope,
                        direction=direction, attribution=attribution, enabled=settings.get(sid, {}).get("enabled", True),
                        signals=(signal_counts or {}).get((sid, direction), 0), executions=m["trades"],
                        wins=m.get("wins", 0), losses=m.get("losses", 0), win_rate=m.get("win_rate"),
                        profit_factor=m.get("profit_factor"), expectancy=m.get("expectancy"),
                        total_pnl=m.get("total_pnl"), total_r=m.get("total_r"), average_pnl=m.get("average_pnl"),
                        average_r=m.get("average_r"), max_drawdown=m.get("max_drawdown"),
                        average_duration=m.get("average_duration"), best_trade=m.get("best"),
                        worst_trade=m.get("worst"), long_trades=m.get("long_trades", 0),
                        short_trades=m.get("short_trades", 0), last_trade=max((t["time"] for t in sel), default=None)
                        if sel and not isinstance(sel[0]["time"], (int, float)) else None,
                        last10=m.get("last10"),
                        extra={"by_regime": {k: metrics(v) for k, v in by_regime.items()}}))


def _write_combos(scope: str, trades: list[dict], timeframe: str = "1h") -> None:
    groups = defaultdict(list)
    for t in trades:
        strats = t["strategies"]
        if len(strats) >= 2:
            groups["+".join(strats)].append(t)
            if len(strats) > 2:
                for pair in combinations(strats, 2):
                    groups["+".join(pair) + " (dentro de grupo maior)"].append(t)
    with session_scope() as db:
        db.execute(delete(StrategyCombination).where(StrategyCombination.scope == scope,
                                                     StrategyCombination.timeframe == timeframe))
        for combo, lst in groups.items():
            m = metrics(lst)
            db.add(StrategyCombination(combination=combo[:200], size=combo.count("+") + 1, scope=scope, timeframe=timeframe,
                                       trades=m["trades"], win_rate=m.get("win_rate"),
                                       profit_factor=m.get("profit_factor"), expectancy=m.get("expectancy"),
                                       pnl_r=m.get("total_r"), max_drawdown=m.get("max_drawdown")))


def refresh_demo() -> dict:
    trades = _demo_trades()
    counts = defaultdict(int)
    with session_scope() as db:
        for sid, d in db.execute(select(StrategySignal.strategy_id, StrategySignal.direction)).all():
            counts[(sid, d)] += 1
            counts[(sid, "ALL")] += 1
    _write_perf("DEMO", trades, counts)
    for tf in registry.TIMEFRAMES:
        _write_combos("DEMO", [t for t in trades if t["timeframe"] == tf], tf)
    update_health()
    return {"demo_trades": len(trades)}


def write_research(trades_hist: list[dict], trades_recent: list[dict], signal_counts: dict,
                   engine_trades: list[dict], timeframe: str = "1h") -> None:
    """No backtest cada estratégia opera isolada: o trade é atribuído só a ela (participant = primary).
    Combinações históricas vêm da simulação do motor (um trade por grupo de confluência)."""
    solo = lambda ts: [{**t, "strategies": [t["primary"]]} for t in ts]  # noqa: E731
    _write_perf("HISTORICAL", solo(trades_hist), signal_counts, timeframe)
    _write_perf("RECENT", solo(trades_recent), None, timeframe)
    _write_combos("HISTORICAL", engine_trades, timeframe)


# ---------------------------------------------------------------- saúde e descoberta
def _perf(scope: str, sid: str, direction: str = "ALL", attribution: str = "primary") -> StrategyPerformance | None:
    with session_scope() as db:
        return db.scalar(select(StrategyPerformance).where(
            StrategyPerformance.scope == scope, StrategyPerformance.strategy_name == sid,
            StrategyPerformance.direction == direction, StrategyPerformance.attribution == attribution))


def classify_discovery(hist_all, hist_long, hist_short, wf_positive_frac: float | None, by_regime: dict) -> list[str]:
    tags = []
    n = hist_all.executions if hist_all else 0
    e = hist_all.expectancy if hist_all and hist_all.expectancy is not None else None
    pf = hist_all.profit_factor if hist_all else None
    if n < 10 or e is None:
        return ["UNVALIDATED"]
    if n >= 30 and e >= 0.10 and (pf or 0) >= 1.3 and (wf_positive_frac or 0) >= 0.6:
        tags.append("BEST")
    elif e > 0:
        tags.append("PROMISING")
    elif n >= 30:
        tags.append("WEAK")
    else:
        tags.append("UNVALIDATED")
    good = [k for k, m in by_regime.items() if m.get("trades", 0) >= 10 and m.get("expectancy", 0) >= 0.15]
    bad = [k for k, m in by_regime.items() if m.get("trades", 0) >= 10 and m.get("expectancy", 0) <= -0.10]
    if good and (bad or e <= 0.05):
        tags.append("REGIME_DEPENDENT")
    el = hist_long.expectancy if hist_long and hist_long.executions >= 15 else None
    es = hist_short.expectancy if hist_short and hist_short.executions >= 15 else None
    if el is not None and el >= 0.10 and (es is None or es <= 0):
        tags.append("LONG_ONLY_STRONG")
    if es is not None and es >= 0.10 and (el is None or el <= 0):
        tags.append("SHORT_ONLY_STRONG")
    return tags


def update_discovery(wf: dict) -> dict:
    out = {}
    with session_scope() as db:
        for s in registry.SIGNAL_STRATEGIES:
            sid = s.strategy_id
            if sid not in wf:   # só as estratégias da pesquisa que acabou de rodar
                continue
            h = _perf("HISTORICAL", sid)
            tags = classify_discovery(h, _perf("HISTORICAL", sid, "LONG"), _perf("HISTORICAL", sid, "SHORT"),
                                      wf.get(sid, {}).get("positive_fraction"),
                                      (h.extra or {}).get("by_regime", {}) if h else {})
            st = db.get(StrategySettings, sid)
            if st:
                st.discovery_tags = tags
            out[sid] = tags
    return out


def update_health() -> dict:
    with session_scope() as db:
        qc = db.get(QuantConfig, 1)
        min_n = qc.health_min_trades if qc else 20
        auto = bool(qc and qc.auto_disable)
    out = {}
    for s in registry.SIGNAL_STRATEGIES:
        sid = s.strategy_id
        demo = _perf("DEMO", sid)
        hist = _perf("HISTORICAL", sid)
        with session_scope() as db:
            st = db.get(StrategySettings, sid)
            if st is None:
                continue
            if not st.enabled:
                st.health, out[sid] = "DISABLED", "DISABLED"
                st.health_reason = st.health_reason or "desativada"
                continue
            src, p = ("DEMO", demo) if demo and demo.executions >= min_n else ("HISTORICAL", hist)
            if not p or p.executions < min_n:
                health, reason = "ACTIVE", f"amostra insuficiente para avaliar (< {min_n} trades)"
            elif (p.expectancy or 0) <= -0.15 and (p.profit_factor or 0) < 0.8:
                health, reason = "DEGRADED", f"{src}: expectativa {p.expectancy:+.2f}R, PF {p.profit_factor} em {p.executions} trades"
            elif (p.expectancy or 0) < 0 or (p.last10 and int(p.last10.split("W")[0]) <= 2):
                health, reason = "WATCH", f"{src}: expectativa {p.expectancy:+.2f}R, últimos 10: {p.last10}"
            else:
                health, reason = "ACTIVE", f"{src}: expectativa {p.expectancy:+.2f}R em {p.executions} trades"
            disable = auto and health == "DEGRADED" and p and p.executions >= 2 * min_n
            st.health, st.health_reason = health, reason
            if disable:
                st.enabled = False
                st.health, st.health_reason = "DISABLED", "auto-desativada (AUTO_DISABLE=true): " + reason
            out[sid] = st.health
        if disable:
            events.emit("STRATEGY_DISABLED", strategy=sid, message="AUTO_DISABLE: " + reason)
    return out


def regime_matrix(scope: str) -> dict:
    """{strategy: {regime: {trades, expectancy, win_rate}}} usando atribuição primária."""
    out = {}
    with session_scope() as db:
        for r in db.scalars(select(StrategyPerformance).where(StrategyPerformance.scope == scope,
                                                              StrategyPerformance.direction == "ALL",
                                                              StrategyPerformance.attribution == "primary")):
            out[r.strategy_name] = {k: {kk: v.get(kk) for kk in ("trades", "expectancy", "win_rate", "total_r")}
                                    for k, v in (r.extra or {}).get("by_regime", {}).items()}
    return out
