"""Tabelas do sistema quantitativo. Todas têm timestamp; estratégias e sinais carregam a versão."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base
from ..models import utcnow


class QuantConfig(Base):
    """Configuração global do motor quantitativo (uma linha)."""
    __tablename__ = "quant_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    engine_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_final_score: Mapped[float] = mapped_column(Float, default=60)
    min_confluence_level: Mapped[str] = mapped_column(String(24), default="CONFLUENCE")  # SINGLE_SIGNAL|CONFLUENCE|STRONG_CONFLUENCE
    single_signal_min_score: Mapped[float] = mapped_column(Float, default=85)   # sinal único só passa com score final >= isto
    weights: Mapped[dict] = mapped_column(JSON, default=lambda: dict(DEFAULT_WEIGHTS))
    max_correlation: Mapped[float] = mapped_column(Float, default=0.85)
    max_open_per_strategy: Mapped[int] = mapped_column(Integer, default=4)
    conflict_margin: Mapped[float] = mapped_column(Float, default=15)           # LONG x SHORT no mesmo par: diferença mínima
    breakeven_at_r: Mapped[float] = mapped_column(Float, default=1.0)           # 0 = desligado
    exit_on_regime_change: Mapped[bool] = mapped_column(Boolean, default=True)
    exit_on_momentum_loss: Mapped[bool] = mapped_column(Boolean, default=True)
    exit_on_opposite_signal: Mapped[bool] = mapped_column(Boolean, default=True)
    # timeframes em que a saída por perda de momentum vale (dados da DEMO: ajuda no 1h, atrapalha no 15m/5m)
    momentum_exit_timeframes: Mapped[list | None] = mapped_column(JSON, nullable=True, default=lambda: ["1h"])
    auto_disable: Mapped[bool] = mapped_column(Boolean, default=False)
    health_min_trades: Mapped[int] = mapped_column(Integer, default=20)
    research_top_n: Mapped[int] = mapped_column(Integer, default=30)
    research_days: Mapped[int] = mapped_column(Integer, default=120)
    research_days_15m: Mapped[int] = mapped_column(Integer, default=60)
    research_days_5m: Mapped[int] = mapped_column(Integer, default=30)
    timeframes: Mapped[list | None] = mapped_column(JSON, nullable=True)   # timeframes ativos; None = todos
    # janela sem NOVAS entradas (horário local; posições abertas continuam protegidas e gerenciadas)
    block_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    block_start_hour: Mapped[int] = mapped_column(Integer, default=23)     # inclusivo
    block_end_hour: Mapped[int] = mapped_column(Integer, default=6)        # exclusivo (06:00 libera)
    block_timezone: Mapped[str] = mapped_column(String(40), default="America/Sao_Paulo")
    # "zerar" a pausa de risco: só trades fechados depois deste instante contam para perda diária/sequência
    risk_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


DEFAULT_WEIGHTS = {"signal": 0.30, "regime": 0.15, "trend": 0.08, "volatility": 0.05, "volume": 0.05,
                   "structure": 0.05, "momentum": 0.07, "confluence": 0.15, "historical": 0.10}


class StrategyDefinition(Base):
    """Definição imutável de cada versão de estratégia (parâmetros, regimes padrão, descrição)."""
    __tablename__ = "strategies"
    __table_args__ = (UniqueConstraint("strategy_id", "version", name="uq_strategy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80))
    family: Mapped[str] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16), default="signal")  # signal | filter
    definition: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StrategySettings(Base):
    """Configuração operacional atual de cada estratégia (o que o usuário controla no painel)."""
    __tablename__ = "strategy_settings"

    strategy_id: Mapped[str] = mapped_column(String(48), primary_key=True)
    active_version: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    min_score: Mapped[float] = mapped_column(Float, default=60)
    timeframe: Mapped[str] = mapped_column(String(8), default="1h")
    allow_long: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_short: Mapped[bool] = mapped_column(Boolean, default=True)
    accepted_regimes: Mapped[list] = mapped_column(JSON, default=list)
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=120)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=6)
    health: Mapped[str] = mapped_column(String(16), default="ACTIVE")  # ACTIVE | WATCH | DEGRADED | DISABLED
    health_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovery_tags: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StrategySignal(Base):
    """Cada sinal emitido por uma estratégia (um por estratégia/par/direção/candle)."""
    __tablename__ = "strategy_signals"
    __table_args__ = (UniqueConstraint("strategy_id", "symbol", "direction", "candle_time", name="uq_signal_candle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    candle_time: Mapped[int] = mapped_column(Integer, index=True)   # abertura (ms) do candle fechado avaliado
    strategy_id: Mapped[str] = mapped_column(String(48), index=True)
    strategy_version: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    timeframe: Mapped[str] = mapped_column(String(8))
    score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    conditions: Mapped[list] = mapped_column(JSON, default=list)   # [[texto, ok], ...]
    regime: Mapped[str] = mapped_column(String(32))
    ref_price: Mapped[float] = mapped_column(Float)
    stop: Mapped[float] = mapped_column(Float)
    target: Mapped[float] = mapped_column(Float)
    expected_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    # ACCEPTED | BELOW_MIN | REGIME_BLOCKED | DIRECTION_DISABLED | USED | EXPIRED
    status: Mapped[str] = mapped_column(String(20), default="ACCEPTED", index=True)
    opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Opportunity(Base):
    """Oportunidade = sinais de uma ou mais estratégias no mesmo par/direção, pontuada e ranqueada."""
    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("symbol", "direction", "timeframe", "candle_time", name="uq_opportunity_tf_candle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    candle_time: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8))
    timeframe: Mapped[str] = mapped_column(String(8))
    confluence_level: Mapped[str] = mapped_column(String(24))
    confluence_group_id: Mapped[str] = mapped_column(String(40))
    primary_strategy: Mapped[str] = mapped_column(String(48))
    supporting_strategies: Mapped[list] = mapped_column(JSON, default=list)
    strategy_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    btc_regime: Mapped[str] = mapped_column(String(32))
    base_score: Mapped[float] = mapped_column(Float)
    regime_score: Mapped[float] = mapped_column(Float)
    confluence_score: Mapped[float] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float, index=True)
    score_components: Mapped[dict] = mapped_column(JSON, default=dict)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RANKED", index=True)  # RANKED|EXECUTED|SKIPPED|CONFLICT|FAILED|EXPIRED
    decision_reasons: Mapped[list] = mapped_column(JSON, default=list)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TradeJournal(Base):
    """Diário completo de cada operação DEMO. Nunca é apagado."""
    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_uid: Mapped[str] = mapped_column(String(40), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="OPEN", index=True)  # OPEN | CLOSED | FAILED
    execution_mode: Mapped[str] = mapped_column(String(8), default="DEMO")
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    direction: Mapped[str] = mapped_column(String(8), index=True)
    timeframe: Mapped[str] = mapped_column(String(8))
    entry_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_entry_price: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    risk_usdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    leverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_primary: Mapped[str] = mapped_column(String(48), index=True)
    strategy_primary_version: Mapped[str] = mapped_column(String(16))
    strategies_supporting: Mapped[list] = mapped_column(JSON, default=list)
    strategy_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    strategy_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    confluence_level: Mapped[str] = mapped_column(String(24))
    confluence_group_id: Mapped[str] = mapped_column(String(40), index=True)
    base_score: Mapped[float] = mapped_column(Float)
    regime_score: Mapped[float] = mapped_column(Float)
    confluence_score: Mapped[float] = mapped_column(Float)
    final_score: Mapped[float] = mapped_column(Float, index=True)
    score_components: Mapped[dict] = mapped_column(JSON, default=dict)
    market_regime: Mapped[str] = mapped_column(String(48))        # estado do próprio par na entrada
    btc_regime: Mapped[str] = mapped_column(String(32), index=True)
    market_state: Mapped[str] = mapped_column(String(16))
    entry_reason: Mapped[str] = mapped_column(Text)
    explanation: Mapped[dict] = mapped_column(JSON, default=dict)
    exit_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)  # EXIT_SIGNAL|STOP|TARGET|TIME_EXIT|REGIME_CHANGE|MOMENTUM_LOSS|MANUAL_DEMO_EXIT|EXTERNAL_CLOSE
    exit_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_reference: Mapped[float] = mapped_column(Float)
    target_reference: Mapped[float] = mapped_column(Float)
    current_stop: Mapped[float | None] = mapped_column(Float, nullable=True)
    mfe_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float | None] = mapped_column(Float, nullable=True)
    funding: Mapped[float | None] = mapped_column(Float, nullable=True)
    slippage_pct: Mapped[float | None] = mapped_column(Float, nullable=True)   # entrada: (preço executado/esperado - 1), a favor < 0
    gross_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)            # líquido (USDT/VST)
    pnl_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    opportunity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    entry_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sl_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tp_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    close_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    breakeven_set: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[list] = mapped_column(JSON, default=list)


class TradeFeatures(Base):
    """Snapshot de TODAS as features e sinais no momento da decisão. Imutável depois de criado."""
    __tablename__ = "trade_features"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trade_journal.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    signals: Mapped[list] = mapped_column(JSON, default=list)
    regime: Mapped[dict] = mapped_column(JSON, default=dict)
    opportunity: Mapped[dict] = mapped_column(JSON, default=dict)


class ImmutableSnapshotError(RuntimeError):
    pass


@event.listens_for(TradeFeatures, "before_update")
def _block_feature_update(mapper, connection, target):  # noqa: ARG001
    raise ImmutableSnapshotError("trade_features é imutável: o snapshot de entrada não pode ser alterado")


class DemoPosition(Base):
    """Estado vivo de cada trade DEMO aberto (atualizado pelo monitor)."""
    __tablename__ = "demo_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trade_journal.id"), unique=True)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    unrealized_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    worst_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    sl_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    open: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StrategyPerformance(Base):
    """Desempenho calculado por estratégia/versão, escopo (DEMO/RECENT/HISTORICAL), direção e atribuição."""
    __tablename__ = "strategy_performance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_name: Mapped[str] = mapped_column(String(48), index=True)
    version: Mapped[str] = mapped_column(String(16))
    scope: Mapped[str] = mapped_column(String(12), index=True)        # DEMO | RECENT | HISTORICAL
    direction: Mapped[str] = mapped_column(String(8), default="ALL")  # ALL | LONG | SHORT
    attribution: Mapped[str] = mapped_column(String(12), default="participant")  # participant | primary
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    signals: Mapped[int] = mapped_column(Integer, default=0)
    executions: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    expectancy: Mapped[float | None] = mapped_column(Float, nullable=True)      # em R
    total_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)       # USDT/VST (DEMO)
    total_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)    # em R
    average_duration: Mapped[float | None] = mapped_column(Float, nullable=True)  # minutos
    best_trade: Mapped[float | None] = mapped_column(Float, nullable=True)
    worst_trade: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_trades: Mapped[int] = mapped_column(Integer, default=0)
    short_trades: Mapped[int] = mapped_column(Integer, default=0)
    last_trade: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last10: Mapped[str | None] = mapped_column(String(16), nullable=True)       # ex.: "7W/3L"
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StrategyCombination(Base):
    """Desempenho por combinação de estratégias (medição apenas; nunca vira estratégia automaticamente)."""
    __tablename__ = "strategy_combinations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    combination: Mapped[str] = mapped_column(String(200), index=True)
    timeframe: Mapped[str] = mapped_column(String(8), default="1h")
    size: Mapped[int] = mapped_column(Integer)
    scope: Mapped[str] = mapped_column(String(12), index=True)
    trades: Mapped[int] = mapped_column(Integer)
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    expectancy: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class EngineCycle(Base):
    """Cada candle processado pelo motor, por timeframe (garante 1 processamento por candle e guarda o resumo)."""
    __tablename__ = "engine_cycles"
    __table_args__ = (UniqueConstraint("timeframe", "candle_time", name="uq_cycle_tf_candle"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    timeframe: Mapped[str] = mapped_column(String(8), index=True)
    candle_time: Mapped[int] = mapped_column(Integer)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)


class MarketRegime(Base):
    """Histórico de regime do BTC (uma linha por candle de 1h avaliado)."""
    __tablename__ = "market_regimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    candle_time: Mapped[int] = mapped_column(Integer, unique=True)
    btc_regime: Mapped[str] = mapped_column(String(32))
    market_state: Mapped[str] = mapped_column(String(16))
    previous_regime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class ResearchRun(Base):
    """Backtest / walk-forward das estratégias (escopo HISTORICAL)."""
    __tablename__ = "research_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    kind: Mapped[str] = mapped_column(String(24), default="backtest_walkforward")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    strategy_versions: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    results: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class QuantEvent(Base):
    """Log estruturado: SIGNAL_GENERATED, OPPORTUNITY_RANKED, TRADE_OPENED, TRADE_UPDATED, TRADE_CLOSED,
    STRATEGY_ENABLED, STRATEGY_DISABLED, REGIME_CHANGED, LIVE_CONFIGURATION_DETECTED, ERROR."""
    __tablename__ = "quant_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    strategy: Mapped[str | None] = mapped_column(String(48), nullable=True, index=True)
    trade_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    report_date: Mapped[str] = mapped_column(String(10), index=True)
    content: Mapped[dict] = mapped_column(JSON, default=dict)


def migrate(engine) -> None:
    """Migrações que o create_all não faz (SQLite não altera constraints): recria `opportunities` com a unicidade
    por timeframe, copiando todas as linhas (nada é perdido). Retomável se for interrompida no meio."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    tables = insp.get_table_names()
    if "opportunities" in tables:
        if "uq_opportunity_tf_candle" in {u["name"] for u in insp.get_unique_constraints("opportunities")}:
            return
        index_names = [ix["name"] for ix in insp.get_indexes("opportunities")]
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE opportunities RENAME TO opportunities_old"))
    elif "opportunities_old" not in tables:
        return
    insp = inspect(engine)
    index_names = [ix["name"] for ix in insp.get_indexes("opportunities_old")]
    cols = [c["name"] for c in insp.get_columns("opportunities_old")]
    with engine.begin() as conn:
        for name in index_names:   # índices do nome antigo impediriam criar os da tabela nova
            conn.execute(text(f"DROP INDEX IF EXISTS {name}"))
    Opportunity.__table__.create(bind=engine)
    collist = ", ".join(cols)
    with engine.begin() as conn:
        conn.execute(text(f"INSERT INTO opportunities ({collist}) SELECT {collist} FROM opportunities_old"))
        n_old = conn.execute(text("SELECT COUNT(*) FROM opportunities_old")).scalar()
        n_new = conn.execute(text("SELECT COUNT(*) FROM opportunities")).scalar()
        if n_old != n_new:
            raise RuntimeError(f"migração de opportunities incompleta ({n_new}/{n_old}); tabela antiga preservada")
        conn.execute(text("DROP TABLE opportunities_old"))
