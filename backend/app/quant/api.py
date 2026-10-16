"""Rotas /api/quant/*. Não existe nenhuma rota que altere o modo de execução (DEMO ONLY nesta versão)."""

import threading
from datetime import timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import String, cast, desc, func, or_, select

from ..common import get_config
from ..database import session_scope
from ..models import utcnow
from . import data, engine, performance, registry, reports, research, safety
from .models import (DemoPosition, EngineCycle, MarketRegime, Opportunity, QuantConfig, QuantEvent, StrategyCombination,
                     StrategyPerformance, StrategySignal, TradeFeatures, TradeJournal)
from .regime import REGIME_LABELS, REGIMES

router = APIRouter(prefix="/api/quant", tags=["quant"])


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


# ---------------------------------------------------------------- visão geral
@router.get("/safety")
def get_safety():
    return {**safety.check_demo_safety(), **data.describe()}


@router.get("/overview")
def overview():
    st = safety.check_demo_safety()
    with session_scope() as db:
        regs = db.scalars(select(MarketRegime).order_by(desc(MarketRegime.candle_time)).limit(48)).all()
        open_rows = db.scalars(select(TradeJournal).where(TradeJournal.status == "OPEN")
                               .order_by(desc(TradeJournal.created_at))).all()
        pos = {p.trade_id: _row(p) for p in db.scalars(select(DemoPosition).where(DemoPosition.open.is_(True)))}
        recent = db.scalars(select(TradeJournal).where(TradeJournal.status != "OPEN")
                            .order_by(desc(TradeJournal.updated_at)).limit(15)).all()
        qc = db.get(QuantConfig, 1)
        totals = db.execute(select(func.count(), func.sum(TradeJournal.pnl), func.sum(TradeJournal.pnl_r))
                            .where(TradeJournal.status == "CLOSED")).one()
        since = utcnow() - timedelta(hours=2)
        top = db.scalars(select(Opportunity).where(Opportunity.created_at >= since)
                         .order_by(desc(Opportunity.final_score)).limit(8)).all()
        cycles = {}
        for tf in registry.TIMEFRAMES:
            c = db.scalar(select(EngineCycle).where(EngineCycle.timeframe == tf)
                          .order_by(desc(EngineCycle.candle_time)).limit(1))
            cycles[tf] = engine.last_cycle.get(tf) or (c.summary if c else None)
        cur = regs[0] if regs else None
        return {
            "safety": st, "data": data.describe(), "engine_enabled": qc.engine_enabled if qc else True,
            "regime": cur and {**_row(cur), "label": REGIME_LABELS.get(cur.btc_regime)},
            "regime_history": [{"candle_time": r.candle_time, "btc_regime": r.btc_regime, "changed": r.changed}
                               for r in reversed(regs)],
            "regime_labels": REGIME_LABELS,
            "last_cycles": cycles, "timeframes": engine.active_timeframes(qc),
            "open_trades": [{**_brief(t), "position": pos.get(t.id)} for t in open_rows],
            "recent_trades": [_brief(t) for t in recent],
            "totals": {"closed": totals[0], "pnl": round(totals[1] or 0, 4), "r": round(totals[2] or 0, 2)},
            "top_opportunities": [_row(o) for o in top],
            "research": {tf: _research_brief(tf) for tf in registry.TIMEFRAMES},
        }


def _brief(t: TradeJournal) -> dict:
    keys = ("id", "trade_uid", "status", "symbol", "direction", "timeframe", "entry_time", "exit_time", "entry_price", "exit_price",
            "expected_entry_price", "quantity", "risk_usdt", "strategy_primary", "strategy_primary_version",
            "strategies_supporting", "strategy_scores", "confluence_level", "confluence_group_id", "final_score",
            "btc_regime", "market_regime", "exit_reason", "exit_detail", "stop_reference", "target_reference",
            "current_stop", "pnl", "pnl_r", "pnl_confirmed", "mfe_r", "mae_r", "duration_minutes", "breakeven_set",
            "slippage_pct", "fees", "funding")
    d = {k: getattr(t, k) for k in keys}
    d["summary"] = (t.explanation or {}).get("summary")
    return d


def _research_brief(tf: str):
    r = research.latest_run(tf)
    if not r:
        return None
    return {k: r[k] for k in ("id", "status", "created_at", "finished_at", "progress", "summary", "error")}


@router.get("/broker")
def broker_view(refresh: bool = False):
    """Posições/ordens/saldo direto da corretora + cruzamento com o diário (fonte da verdade do painel)."""
    from . import broker
    if refresh:
        broker.invalidate()
    return broker.snapshot()


@router.get("/broker/result")
def broker_result(refresh: bool = False):
    """Resultado realizado REAL (lançamentos da corretora) comparado com o diário."""
    from . import broker
    if refresh:
        broker.invalidate()
    return broker.result()


@router.get("/health")
def health():
    """Saúde operacional: último ciclo, erros de conexão recentes e conciliação com a corretora."""
    from . import monitor
    with session_scope() as db:
        last = db.scalar(select(EngineCycle).order_by(desc(EngineCycle.created_at)).limit(1))
        since = utcnow() - timedelta(minutes=30)
        errs = db.scalars(select(QuantEvent).where(QuantEvent.event_type == "ERROR", QuantEvent.created_at >= since)
                          .order_by(desc(QuantEvent.id)).limit(5)).all()
        last_at = last.created_at if last else None
    age = None
    if last_at is not None:
        from datetime import timezone as _tz
        la = last_at if last_at.tzinfo else last_at.replace(tzinfo=_tz.utc)
        age = round((utcnow() - la).total_seconds() / 60, 1)
    net = [e for e in errs if "NETWORK" in str((e.details or {}).get("message", "")) or
           "getaddrinfo" in str((e.details or {}).get("message", ""))]
    return {"last_cycle_minutes_ago": age, "stale": age is None or age > 12,
            "recent_errors": [{"at": e.created_at.isoformat(), "message": (e.details or {}).get("message")} for e in errs],
            "network_problem": bool(net), "orphans": monitor.broker_state.get("orphans", []),
            "reconciled_at": monitor.broker_state.get("checked_at")}


@router.post("/risk/reset")
def reset_risk():
    """Libera novas entradas agora: a perda do dia e a sequência de perdas recomeçam a contar a partir deste
    instante. Nenhum trade ou histórico é apagado."""
    with session_scope() as db:
        qc = db.get(QuantConfig, 1)
        qc.risk_reset_at = utcnow()
        at = qc.risk_reset_at.isoformat()
    from . import events
    events.emit("STRATEGY_UPDATED", message="pausa de risco zerada pelo usuário (histórico preservado)", reset_at=at)
    return {"reset_at": at, "risk_paused": engine._risk_pause(get_config(), None, utcnow()),
            "blocked_hours": engine.blocked_hours(registry.quant_config())}


@router.get("/risk")
def risk_status():
    qc = registry.quant_config()
    return {"risk_paused": engine._risk_pause(get_config(), None, qc.risk_reset_at),
            "blocked_hours": engine.blocked_hours(qc), "risk_reset_at": qc.risk_reset_at,
            "block": {"enabled": qc.block_hours_enabled, "start": qc.block_start_hour, "end": qc.block_end_hour,
                      "timezone": qc.block_timezone}}


@router.post("/run")
def run_now():
    """Força um ciclo agora (reprocessa o último candle fechado; sinais/oportunidades não duplicam)."""
    threading.Thread(target=engine.run_engine, kwargs={"force": True}, daemon=True).start()
    return {"started": True}


# ---------------------------------------------------------------- estratégias
def _perf_rows(sid: str) -> dict:
    out = {}
    with session_scope() as db:
        for r in db.scalars(select(StrategyPerformance).where(StrategyPerformance.strategy_name == sid)):
            out.setdefault(r.scope, {}).setdefault(r.attribution, {})[r.direction] = _row(r)
    return out


@router.get("/strategies")
def list_strategies():
    registry.sync_definitions()
    sm = registry.settings_map()
    out = []
    with session_scope() as db:
        counts = {(s, st): n for s, st, n in db.execute(
            select(StrategySignal.strategy_id, StrategySignal.status, func.count())
            .group_by(StrategySignal.strategy_id, StrategySignal.status)).all()}
        open_by = dict(db.execute(select(TradeJournal.strategy_primary, func.count())
                                  .where(TradeJournal.status == "OPEN").group_by(TradeJournal.strategy_primary)).all())
    for s in registry.STRATEGIES:
        sid = s.strategy_id
        out.append({
            **s.get_metadata(), "settings": sm.get(sid), "blocked_reason": registry.blocked_reason(sid),
            "performance": _perf_rows(sid),
            "signals": {st: n for (x, st), n in counts.items() if x == sid},
            "open_trades": open_by.get(sid, 0),
            "discovery_help": {t: performance.DISCOVERY_HELP.get(t) for t in (sm.get(sid) or {}).get("discovery_tags") or []},
        })
    return {"strategies": out, "regimes": REGIMES, "regime_labels": REGIME_LABELS,
            "health_help": performance.HEALTH_HELP, "discovery_help": performance.DISCOVERY_HELP}


@router.get("/strategies/{sid}")
def strategy_detail(sid: str):
    s = registry.BY_ID.get(sid)
    if not s:
        raise HTTPException(404, "estratégia não encontrada")
    with session_scope() as db:
        sigs = db.scalars(select(StrategySignal).where(StrategySignal.strategy_id == sid)
                          .order_by(desc(StrategySignal.id)).limit(60)).all()
        trades = db.scalars(select(TradeJournal).where(or_(TradeJournal.strategy_primary == sid,
                                                           cast(TradeJournal.strategies_supporting, String).like(f'%"{sid}"%')))
                            .order_by(desc(TradeJournal.id)).limit(60)).all()
        sig_rows = [_row(x) for x in sigs]
        trade_rows = [_brief(t) | {"role": "primária" if t.strategy_primary == sid else "apoio"} for t in trades]
    r = research.latest_run(s.timeframe)
    return {"metadata": s.get_metadata(), "settings": registry.settings_map().get(sid),
            "blocked_reason": registry.blocked_reason(sid), "versions": registry.definitions_history(sid),
            "performance": _perf_rows(sid), "signals": sig_rows, "trades": trade_rows,
            "research": (r or {}).get("results", {}) and (r["results"].get("per_strategy") or {}).get(sid)}


class StrategyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # parâmetros da estratégia só mudam com nova versão
    enabled: bool | None = None
    min_score: float | None = Field(None, ge=0, le=100)
    allow_long: bool | None = None
    allow_short: bool | None = None
    accepted_regimes: list[str] | None = None
    cooldown_minutes: int | None = Field(None, ge=0, le=10080)
    max_trades_per_day: int | None = Field(None, ge=0, le=100)


@router.put("/strategies/{sid}")
def update_strategy(sid: str, body: StrategyUpdate):
    try:
        return registry.update_settings(sid, body.model_dump(exclude_none=True))
    except KeyError:
        raise HTTPException(404, "estratégia não encontrada")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- sinais e oportunidades
@router.get("/opportunities")
def opportunities(timeframe: str = "1h", candle_time: int | None = None, limit: int = 100):
    with session_scope() as db:
        ct = candle_time or db.scalar(select(func.max(StrategySignal.candle_time)).where(StrategySignal.timeframe == timeframe))
        candles = [c for (c,) in db.execute(select(StrategySignal.candle_time).where(StrategySignal.timeframe == timeframe)
                                             .distinct().order_by(desc(StrategySignal.candle_time)).limit(48)).all()]
        if not ct:
            return {"candle_time": None, "candles": [], "opportunities": [], "signals": [], "signal_status": {}}
        opps = db.scalars(select(Opportunity).where(Opportunity.candle_time == ct, Opportunity.timeframe == timeframe)
                          .order_by(Opportunity.rank).limit(limit)).all()
        sigs = db.scalars(select(StrategySignal).where(StrategySignal.candle_time == ct,
                                                       StrategySignal.timeframe == timeframe)).all()
        by_status = {}
        for s in sigs:
            by_status[s.status] = by_status.get(s.status, 0) + 1
        return {"candle_time": ct, "candles": candles, "opportunities": [_row(o) for o in opps],
                "signals": [_row(s) for s in sigs], "signal_status": by_status}


@router.get("/signals")
def signals(strategy: str | None = None, symbol: str | None = None, status: str | None = None, limit: int = 200):
    q = select(StrategySignal).order_by(desc(StrategySignal.id)).limit(min(limit, 1000))
    if strategy:
        q = q.where(StrategySignal.strategy_id == strategy)
    if symbol:
        q = q.where(StrategySignal.symbol == symbol.upper())
    if status:
        q = q.where(StrategySignal.status == status)
    with session_scope() as db:
        return [_row(s) for s in db.scalars(q)]


# ---------------------------------------------------------------- diário
@router.get("/journal")
def journal(q: str | None = None, status: str | None = None, strategy: str | None = None,
            direction: str | None = None, regime: str | None = None, exit_reason: str | None = None,
            timeframe: str | None = None, limit: int = 200):
    stmt = select(TradeJournal).order_by(desc(TradeJournal.id)).limit(min(limit, 1000))
    if q:
        like = f"%{q.upper()}%"
        stmt = stmt.where(or_(TradeJournal.symbol.like(like), TradeJournal.trade_uid.like(f"%{q}%"),
                              TradeJournal.strategy_primary.like(like), TradeJournal.entry_reason.like(f"%{q}%")))
    if status:
        stmt = stmt.where(TradeJournal.status == status)
    if strategy:
        stmt = stmt.where(or_(TradeJournal.strategy_primary == strategy,
                              cast(TradeJournal.strategies_supporting, String).like(f'%"{strategy}"%')))
    if direction:
        stmt = stmt.where(TradeJournal.direction == direction)
    if regime:
        stmt = stmt.where(TradeJournal.btc_regime == regime)
    if exit_reason:
        stmt = stmt.where(TradeJournal.exit_reason == exit_reason)
    if timeframe:
        stmt = stmt.where(TradeJournal.timeframe == timeframe)
    with session_scope() as db:
        return [_brief(t) for t in db.scalars(stmt)]


@router.get("/journal/{trade_id}")
def journal_detail(trade_id: int):
    with session_scope() as db:
        t = db.get(TradeJournal, trade_id)
        if not t:
            raise HTTPException(404, "trade não encontrado")
        feats = db.scalar(select(TradeFeatures).where(TradeFeatures.trade_id == trade_id))
        pos = db.scalar(select(DemoPosition).where(DemoPosition.trade_id == trade_id))
        evs = db.scalars(select(QuantEvent).where(QuantEvent.trade_id == trade_id).order_by(QuantEvent.id)).all()
        return {"trade": _row(t), "features_at_entry": feats and _row(feats), "position": pos and _row(pos),
                "events": [_row(e) for e in evs]}


@router.post("/journal/{trade_id}/close")
def manual_close(trade_id: int):
    """MANUAL_DEMO_EXIT: fecha a posição na conta DEMO."""
    from .execution import get_adapter
    try:
        res = get_adapter().close_trade(trade_id, "MANUAL_DEMO_EXIT", "fechado manualmente pelo painel")
    except safety.LiveExecutionBlocked as exc:
        raise HTTPException(403, str(exc))
    if not res.get("success"):
        raise HTTPException(400, res.get("error"))
    return res


# ---------------------------------------------------------------- desempenho
@router.get("/performance")
def perf(scope: str = "DEMO"):
    scope = scope.upper()
    if scope not in ("DEMO", "RECENT", "HISTORICAL"):
        raise HTTPException(400, "scope deve ser DEMO, RECENT ou HISTORICAL")
    if scope == "DEMO":
        performance.refresh_demo()
    with session_scope() as db:
        rows = db.scalars(select(StrategyPerformance).where(StrategyPerformance.scope == scope)).all()
        combos = db.scalars(select(StrategyCombination).where(StrategyCombination.scope == scope)
                            .order_by(desc(StrategyCombination.trades))).all()
        return {"scope": scope, "rows": [_row(r) for r in rows], "combinations": [_row(c) for c in combos],
                "regime_matrix": performance.regime_matrix(scope), "regimes": REGIMES, "regime_labels": REGIME_LABELS}


# ---------------------------------------------------------------- pesquisa e relatórios
class ResearchRequest(BaseModel):
    days: int | None = Field(None, ge=7, le=365)
    top_n: int | None = Field(None, ge=5, le=100)
    timeframes: list[str] | None = None


@router.post("/research")
def start_research(body: ResearchRequest):
    tfs = body.timeframes or registry.TIMEFRAMES
    if any(tf not in registry.TIMEFRAMES for tf in tfs):
        raise HTTPException(400, f"timeframes válidos: {registry.TIMEFRAMES}")
    if not research.start_background(body.days, body.top_n, tfs):
        raise HTTPException(409, "pesquisa já em execução")
    return {"started": True, "timeframes": tfs}


@router.get("/research")
def get_research(timeframe: str = "1h"):
    return research.latest_run(timeframe)


@router.get("/reports")
def get_reports(limit: int = 30):
    return reports.list_reports(limit)


@router.post("/reports")
def make_report(day: str | None = None):
    return reports.generate(day)


@router.get("/events")
def get_events(event_type: str | None = None, symbol: str | None = None, limit: int = 200):
    q = select(QuantEvent).order_by(desc(QuantEvent.id)).limit(min(limit, 1000))
    if event_type:
        q = q.where(QuantEvent.event_type == event_type)
    if symbol:
        q = q.where(QuantEvent.symbol == symbol.upper())
    with session_scope() as db:
        return [_row(e) for e in db.scalars(q)]


# ---------------------------------------------------------------- configuração do motor
READ_ONLY_CONFIG = {"id", "updated_at", "risk_reset_at", "block_timezone"}


class QuantConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")  # não existe campo de modo LIVE

    @model_validator(mode="before")
    @classmethod
    def _drop_read_only(cls, data):
        # a tela pode reenviar campos que só o servidor controla: são ignorados (qualquer outro campo desconhecido,
        # como live_trading, continua sendo recusado)
        return {k: v for k, v in data.items() if k not in READ_ONLY_CONFIG} if isinstance(data, dict) else data
    engine_enabled: bool | None = None
    min_final_score: float | None = Field(None, ge=0, le=100)
    min_confluence_level: str | None = Field(None, pattern=r"^(SINGLE_SIGNAL|CONFLUENCE|STRONG_CONFLUENCE)$")
    single_signal_min_score: float | None = Field(None, ge=0, le=100)
    weights: dict[str, float] | None = None
    max_correlation: float | None = Field(None, ge=0, le=1)
    max_open_per_strategy: int | None = Field(None, ge=1, le=20)
    conflict_margin: float | None = Field(None, ge=0, le=100)
    breakeven_at_r: float | None = Field(None, ge=0, le=10)
    exit_on_regime_change: bool | None = None
    exit_on_momentum_loss: bool | None = None
    exit_on_opposite_signal: bool | None = None
    auto_disable: bool | None = None
    health_min_trades: int | None = Field(None, ge=5, le=500)
    research_top_n: int | None = Field(None, ge=5, le=100)
    research_days: int | None = Field(None, ge=30, le=365)
    research_days_15m: int | None = Field(None, ge=7, le=180)
    research_days_5m: int | None = Field(None, ge=7, le=90)
    timeframes: list[str] | None = None
    momentum_exit_timeframes: list[str] | None = None
    block_hours_enabled: bool | None = None
    block_start_hour: int | None = Field(None, ge=0, le=23)
    block_end_hour: int | None = Field(None, ge=0, le=23)


@router.get("/config")
def get_qconfig():
    with session_scope() as db:
        return _row(db.get(QuantConfig, 1))


@router.put("/config")
def put_qconfig(body: QuantConfigUpdate):
    changes = body.model_dump(exclude_none=True)
    if "timeframes" in changes and (not changes["timeframes"]
                                    or any(tf not in registry.TIMEFRAMES for tf in changes["timeframes"])):
        raise HTTPException(400, f"escolha ao menos um timeframe entre {registry.TIMEFRAMES}")
    if "weights" in changes:
        from .models import DEFAULT_WEIGHTS
        bad = [k for k in changes["weights"] if k not in DEFAULT_WEIGHTS]
        if bad or any(v < 0 for v in changes["weights"].values()):
            raise HTTPException(400, f"pesos inválidos: {bad or 'valores negativos'}")
    with session_scope() as db:
        qc = db.get(QuantConfig, 1)
        for k, v in changes.items():
            setattr(qc, k, v)
        return _row(qc)
