from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from . import botlog, settings
from .bingx import get_client
from .database import init_db, session_scope
from .backtest import start_backtest
from .models import BacktestRun, BotConfig, BotLog, Position, Signal, Trade
from .performance import build_report
from .risk import risk_state
from .quant import registry as quant_registry
from .quant import safety as quant_safety
from .quant.api import router as quant_router
from .quant.models import ResearchRun
from .scheduler import JOBS, job_status, next_runs, run_job, scheduler, start_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with session_scope() as db:  # backtests interrompidos por reinício
        for run in db.scalars(select(BacktestRun).where(BacktestRun.status == "running")):
            run.status, run.error = "error", "Interrompido pelo reinício do bot"
        for run in db.scalars(select(ResearchRun).where(ResearchRun.status == "running")):
            run.status, run.error = "error", "Interrompida pelo reinício do bot"
    quant_registry.sync_definitions()
    st = quant_safety.check_demo_safety()
    if st["safety_ok"]:
        botlog.info("api", "Modo de execução: DEMO ONLY · LIVE EXECUTION: BLOCKED · análise com dados da conta REAL")
    else:
        botlog.critical("api", "LIVE_CONFIGURATION_DETECTED: motor travado, nenhuma ordem será enviada — "
                               + "; ".join(st["reasons"]))
    mode = "DEMO (VST)" if settings.BINGX_DEMO else "REAL"
    botlog.info("api", f"Bot iniciado - conta BingX {mode}")
    if settings.SCHEDULER_ENABLED:
        start_scheduler()
    yield
    if scheduler.running:
        scheduler.shutdown(wait=False)


app = FastAPI(title="BingX Trading Bot", lifespan=lifespan)
app.include_router(quant_router)
@app.middleware("http")
async def require_token(request, call_next):
    """Com BOT_API_TOKEN definido, toda rota /api exige o header X-Bot-Token (comparação em tempo constante)."""
    import hmac
    from fastapi.responses import JSONResponse
    if settings.BOT_API_TOKEN and request.url.path.startswith("/api"):
        got = request.headers.get("x-bot-token", "")
        if not hmac.compare_digest(got, settings.BOT_API_TOKEN):
            return JSONResponse({"detail": "não autorizado"}, status_code=401)
    return await call_next(request)


app.add_middleware(
    CORSMiddleware, allow_origins=[settings.DASHBOARD_ORIGIN], allow_methods=["*"], allow_headers=["*"],
)


def _row(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}


class ConfigUpdate(BaseModel):
    automation_enabled: bool | None = None
    max_positions: int | None = Field(None, ge=1, le=20)
    min_execute_score: float | None = Field(None, ge=0, le=145)
    leverage: int | None = Field(None, ge=1, le=125)
    capital_percent: float | None = Field(None, gt=0, le=100)
    news_check_enabled: bool | None = None
    timeframe: str | None = Field(None, pattern=r"^(1m|3m|5m|15m|30m|1h|2h|4h|6h|12h|1d)$")
    scan_top_n: int | None = Field(None, ge=1, le=300)
    min_volume_usdt: float | None = Field(None, ge=0)
    max_spread_pct: float | None = Field(None, gt=0)
    max_funding_pct: float | None = Field(None, gt=0)
    block_hours_enabled: bool | None = None
    block_hour_start: int | None = Field(None, ge=0, le=23)
    block_hour_end: int | None = Field(None, ge=0, le=23)
    signal_expiry_minutes: int | None = Field(None, ge=1, le=1440)
    market_data_live: bool | None = None
    min_breadth: int | None = Field(None, ge=1, le=50)
    min_adx: float | None = Field(None, ge=0, le=100)
    htf_filter_enabled: bool | None = None
    htf_timeframe: str | None = Field(None, pattern=r"^(15m|30m|1h|2h|4h|12h|1d)$")
    btc_filter_enabled: bool | None = None
    require_confirmation_candle: bool | None = None
    min_ema50_slope_pct: float | None = Field(None, ge=0, le=5)
    min_stop_cost_ratio: float | None = Field(None, ge=0, le=50)
    fee_rate_pct: float | None = Field(None, ge=0, le=1)
    structural_stop: bool | None = None
    max_stop_atr: float | None = Field(None, ge=1.5, le=10)
    breakeven_after_tp1: bool | None = None
    sizing_mode: str | None = Field(None, pattern=r"^(risk|capital)$")
    risk_per_trade_pct: float | None = Field(None, gt=0, le=10)
    symbol_cooldown_minutes: int | None = Field(None, ge=0, le=1440)
    max_same_direction: int | None = Field(None, ge=1, le=20)
    daily_loss_limit_pct: float | None = Field(None, ge=0, le=100)
    max_consecutive_losses: int | None = Field(None, ge=0, le=50)
    news_top_candidates: int | None = Field(None, ge=1, le=5)
    news_block_generic: bool | None = None


class BacktestRequest(BaseModel):
    timeframe: str | None = None
    days: int | None = Field(None, ge=1, le=180)
    top_n: int | None = Field(None, ge=1, le=150)
    symbols: list[str] | None = None
    spread_pct: float | None = Field(None, ge=0, le=1)
    risk_pct: float | None = Field(None, gt=0, le=10)


@app.get("/api/status")
def status():
    client = get_client()
    balance, balance_error = None, None
    if client.has_credentials:
        try:
            balance = client.get_balance()
        except Exception as exc:
            balance_error = str(exc)
    else:
        balance_error = "Credenciais BingX não configuradas"
    with session_scope() as db:
        cfg = db.get(BotConfig, 1)
        open_positions = len(db.scalars(select(Position).where(Position.status == "open")).all())
        approved = len(db.scalars(select(Signal).where(Signal.status == "approved")).all())
    return {
        "risk": risk_state(cfg, balance["equity"] if balance else None),
        "mode": "demo" if settings.BINGX_DEMO else "live",
        "automation_enabled": cfg.automation_enabled,
        "scheduler_running": scheduler.running,
        "news_llm_configured": settings.NEWS_ENABLED and bool(settings.NVIDIA_API_KEY),
        "balance": balance,
        "balance_error": balance_error,
        "open_positions": open_positions,
        "approved_signals": approved,
        "jobs": {name: {**job_status[name], "next_run": nxt} for name, nxt in next_runs().items()},
    }


@app.get("/api/config")
def get_config_route():
    with session_scope() as db:
        return _row(db.get(BotConfig, 1))


@app.put("/api/config")
def update_config(payload: ConfigUpdate):
    changes = payload.model_dump(exclude_none=True)
    with session_scope() as db:
        cfg = db.get(BotConfig, 1)
        for key, value in changes.items():
            setattr(cfg, key, value)
        data = _row(cfg)
    botlog.info("api", f"Configuração alterada: {changes}")
    return data


@app.get("/api/signals")
def list_signals(status: str | None = None, limit: int = 100):
    with session_scope() as db:
        q = select(Signal).order_by(desc(Signal.created_at)).limit(limit)
        if status:
            q = q.where(Signal.status == status)
        return [_row(s) for s in db.scalars(q)]


@app.post("/api/signals/{signal_id}/execute")
def execute_signal_route(signal_id: int):
    # Sistema anterior desativado: toda execução passa pelo motor quant (filtros, risco, diário e conciliação).
    raise HTTPException(410, "execução manual de sinais do sistema anterior foi desativada; use o motor quant")


@app.post("/api/signals/{signal_id}/cancel")
def cancel_signal(signal_id: int):
    with session_scope() as db:
        sig = db.get(Signal, signal_id)
        if sig is None:
            raise HTTPException(404, "Sinal não encontrado")
        if sig.status != "approved":
            raise HTTPException(400, f"Sinal está '{sig.status}'")
        sig.status = "cancelled"
        sig.status_reason = "Cancelado manualmente"
        return _row(sig)


@app.get("/api/positions")
def list_positions(status: str | None = "open", limit: int = 100):
    with session_scope() as db:
        q = select(Position).order_by(desc(Position.opened_at)).limit(limit)
        if status and status != "all":
            q = q.where(Position.status == status)
        return [_row(p) for p in db.scalars(q)]


@app.get("/api/trades")
def list_trades(limit: int = 100):
    with session_scope() as db:
        return [_row(t) for t in db.scalars(select(Trade).order_by(desc(Trade.created_at)).limit(limit))]


@app.get("/api/logs")
def list_logs(limit: int = 200, module: str | None = None):
    with session_scope() as db:
        q = select(BotLog).order_by(desc(BotLog.id)).limit(limit)
        if module:
            q = q.where(BotLog.module == module)
        return [_row(entry) for entry in db.scalars(q)]


@app.get("/api/performance")
def performance(days: int | None = None):
    return build_report(days)


@app.post("/api/backtest")
def backtest_start(payload: BacktestRequest):
    try:
        return {"id": start_backtest(payload.model_dump(exclude_none=True))}
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/backtest")
def backtest_list(limit: int = 20):
    with session_scope() as db:
        runs = db.scalars(select(BacktestRun).order_by(desc(BacktestRun.id)).limit(limit))
        return [{k: v for k, v in _row(r).items() if k != "trades"} for r in runs]


@app.get("/api/backtest/{run_id}")
def backtest_get(run_id: int):
    with session_scope() as db:
        run = db.get(BacktestRun, run_id)
        if run is None:
            raise HTTPException(404, "Backtest não encontrado")
        return _row(run)


@app.post("/api/jobs/{name}")
def trigger_job(name: str):
    if name not in JOBS:
        raise HTTPException(404, f"Job desconhecido. Use: {', '.join(JOBS)}")
    return run_job(name)
