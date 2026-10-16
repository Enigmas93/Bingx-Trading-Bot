from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BotConfig(Base):
    __tablename__ = "bot_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Execução
    max_positions: Mapped[int] = mapped_column(Integer, default=3)
    min_execute_score: Mapped[float] = mapped_column(Float, default=65)
    leverage: Mapped[int] = mapped_column(Integer, default=10)
    capital_percent: Mapped[float] = mapped_column(Float, default=10.0)  # percentual_config
    news_check_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Scanner
    timeframe: Mapped[str] = mapped_column(String(8), default="15m")
    scan_top_n: Mapped[int] = mapped_column(Integer, default=40)
    min_volume_usdt: Mapped[float] = mapped_column(Float, default=1_000_000)
    max_spread_pct: Mapped[float] = mapped_column(Float, default=0.1)
    max_funding_pct: Mapped[float] = mapped_column(Float, default=0.1)
    block_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    block_hour_start: Mapped[int] = mapped_column(Integer, default=0)  # UTC, inclusivo
    block_hour_end: Mapped[int] = mapped_column(Integer, default=5)  # UTC, inclusivo (00:00-05:59)
    signal_expiry_minutes: Mapped[int] = mapped_column(Integer, default=30)

    # Dados de mercado: True = análise com dados da conta REAL (execução continua na conta configurada)
    market_data_live: Mapped[bool] = mapped_column(Boolean, default=True)

    # Filtros de qualidade do sinal
    min_adx: Mapped[float] = mapped_column(Float, default=40)  # ADX mínimo para entrar (backtest: 40 = melhor)
    htf_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # tendência no timeframe maior
    htf_timeframe: Mapped[str] = mapped_column(String(8), default="1h")
    btc_filter_enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # alts seguem o BTC
    require_confirmation_candle: Mapped[bool] = mapped_column(Boolean, default=True)
    min_ema50_slope_pct: Mapped[float] = mapped_column(Float, default=0.02)
    min_stop_cost_ratio: Mapped[float] = mapped_column(Float, default=4.0)  # stop >= N x (spread + taxas)
    fee_rate_pct: Mapped[float] = mapped_column(Float, default=0.05)  # taxa taker por lado

    # Gestão do trade
    structural_stop: Mapped[bool] = mapped_column(Boolean, default=True)
    max_stop_atr: Mapped[float] = mapped_column(Float, default=3.0)
    breakeven_after_tp1: Mapped[bool] = mapped_column(Boolean, default=True)
    sizing_mode: Mapped[str] = mapped_column(String(10), default="risk")  # risk | capital
    risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=0.5)

    # Amplitude: só executa quando pelo menos N pares deram sinal no mesmo candle (mercado em tendência)
    min_breadth: Mapped[int] = mapped_column(Integer, default=2)

    # Controle de risco
    symbol_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=90)
    max_same_direction: Mapped[int] = mapped_column(Integer, default=3)
    daily_loss_limit_pct: Mapped[float] = mapped_column(Float, default=5.0)
    max_consecutive_losses: Mapped[int] = mapped_column(Integer, default=4)

    # Notícias
    news_top_candidates: Mapped[int] = mapped_column(Integer, default=3)
    news_block_generic: Mapped[bool] = mapped_column(Boolean, default=True)  # bloquear sentimento negativo genérico

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))  # LONG | SHORT
    # approved | executed | failed | cancelled | expired
    status: Mapped[str] = mapped_column(String(16), default="approved", index=True)
    confidence_score: Mapped[float] = mapped_column(Float)
    min_score_required: Mapped[float] = mapped_column(Float)
    adx: Mapped[float] = mapped_column(Float)
    atr: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float)
    tp2: Mapped[float] = mapped_column(Float)
    tp3: Mapped[float] = mapped_column(Float)
    indicators: Mapped[dict] = mapped_column(JSON, default=dict)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    news_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int | None] = mapped_column(ForeignKey("signals.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    leverage: Mapped[int] = mapped_column(Integer)
    margin_mode: Mapped[str] = mapped_column(String(16), default="CROSSED")
    stop_loss: Mapped[float] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float)
    tp2: Mapped[float] = mapped_column(Float)
    tp3: Mapped[float] = mapped_column(Float)
    entry_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sl_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tp1_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tp2_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tp3_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | closed
    errors: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp1: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp2: Mapped[float | None] = mapped_column(Float, nullable=True)
    tp3: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    margin: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    protected: Mapped[bool] = mapped_column(Boolean, default=True)  # False = sem Stop Loss na corretora
    source: Mapped[str] = mapped_column(String(16), default="bot")  # bot | external
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | closed
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Resultado real (BingX income): realized_pnl = gross_pnl + fees + funding
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)  # False = estimativa
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # SL, TP1+BE, TP3, MANUAL...
    tp1_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    breakeven_set: Mapped[bool] = mapped_column(Boolean, default=False)
    risk_amount: Mapped[float | None] = mapped_column(Float, nullable=True)  # USDT em risco na entrada


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | error
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    trades: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BotLog(Base):
    __tablename__ = "bot_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    level: Mapped[str] = mapped_column(String(10))
    module: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
