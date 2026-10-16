// Tipos e rótulos do motor quant multi-estratégia (DEMO ONLY).

export type Dir = "LONG" | "SHORT";

export const TIMEFRAMES = ["1h", "15m", "5m"] as const;
export const TF_LABEL: Record<string, string> = { "1h": "1 hora", "15m": "15 min", "5m": "5 min" };

export type Safety = {
  execution_mode: "DEMO_ONLY";
  live_execution: "BLOCKED";
  safety_ok: boolean;
  engine_state: "READY" | "LOCKED";
  reasons: string[];
  account: string;
  execution_endpoint: string;
  demo_mode: boolean;
  live_trading: boolean;
};

export type DataInfo = {
  analysis_data: { name: string; endpoint: string; access: string };
  execution: { name: string; endpoint: string };
};

export type TradeBrief = {
  id: number;
  trade_uid: string;
  status: "OPEN" | "CLOSED" | "FAILED";
  symbol: string;
  direction: Dir;
  timeframe: string;
  entry_time: string | null;
  exit_time: string | null;
  entry_price: number | null;
  exit_price: number | null;
  expected_entry_price: number;
  quantity: number;
  risk_usdt: number | null;
  strategy_primary: string;
  strategy_primary_version: string;
  strategies_supporting: string[];
  strategy_scores: Record<string, number>;
  confluence_level: string;
  confluence_group_id: string;
  final_score: number;
  btc_regime: string;
  market_regime: string;
  exit_reason: string | null;
  exit_detail: string | null;
  stop_reference: number;
  target_reference: number;
  current_stop: number | null;
  pnl: number | null;
  pnl_r: number | null;
  pnl_confirmed: boolean;
  mfe_r: number | null;
  mae_r: number | null;
  duration_minutes: number | null;
  breakeven_set: boolean;
  slippage_pct: number | null;
  fees: number | null;
  funding: number | null;
  summary: string | null;
  role?: string;
  position?: {
    mark_price: number | null;
    unrealized_pnl: number | null;
    unrealized_r: number | null;
    sl_verified_at: string | null;
  } | null;
};

export type Opportunity = {
  id: number;
  timeframe: string;
  candle_time: number;
  symbol: string;
  direction: Dir;
  confluence_level: string;
  confluence_group_id: string;
  primary_strategy: string;
  supporting_strategies: string[];
  strategy_scores: Record<string, number>;
  btc_regime: string;
  base_score: number;
  regime_score: number;
  confluence_score: number;
  final_score: number;
  score_components: Record<string, number | Record<string, number>>;
  rank: number;
  status: string;
  decision_reasons: string[];
  trade_id: number | null;
};

export type StrategySignal = {
  id: number;
  candle_time: number;
  strategy_id: string;
  strategy_version: string;
  symbol: string;
  direction: Dir;
  score: number;
  confidence: number;
  reason: string;
  conditions: [string, boolean][];
  regime: string;
  ref_price: number;
  stop: number;
  target: number;
  expected_r: number | null;
  status: string;
  opportunity_id: number | null;
  created_at: string;
};

export type PerfRow = {
  strategy_name: string;
  version: string;
  scope: string;
  direction: "ALL" | Dir;
  attribution: "participant" | "primary";
  enabled: boolean;
  signals: number;
  executions: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  total_pnl: number | null;
  total_r: number | null;
  average_r: number | null;
  max_drawdown: number | null;
  average_duration: number | null;
  best_trade: number | null;
  worst_trade: number | null;
  long_trades: number;
  short_trades: number;
  last10: string | null;
  last_trade: string | null;
  extra: { by_regime?: Record<string, Metrics> };
};

export type Metrics = {
  trades: number;
  wins?: number;
  losses?: number;
  win_rate?: number;
  profit_factor?: number | null;
  expectancy?: number;
  total_r?: number;
  max_drawdown?: number;
  best?: number;
  worst?: number;
  long_trades?: number;
  short_trades?: number;
  last10?: string;
  total_pnl?: number | null;
};

export type StrategySettings = {
  strategy_id: string;
  active_version: string;
  enabled: boolean;
  min_score: number;
  timeframe: string;
  allow_long: boolean;
  allow_short: boolean;
  accepted_regimes: string[];
  cooldown_minutes: number;
  max_trades_per_day: number;
  health: string;
  health_reason: string | null;
  discovery_tags: string[];
};

export type StrategyInfo = {
  strategy_id: string;
  base_id: string;
  name: string;
  version: string;
  family: string;
  role: "signal" | "filter";
  timeframe: string;
  supported_directions: Dir[];
  default_regimes: string[];
  default_min_score: number;
  max_hold_bars: number;
  exit_style: string;
  description: string;
  long_params: Record<string, number | boolean>;
  short_params: Record<string, number | boolean>;
  settings: StrategySettings | null;
  blocked_reason: string | null;
  performance: Record<string, Record<string, Record<string, PerfRow>>>;
  signals: Record<string, number>;
  open_trades: number;
};

export type StrategiesResponse = {
  strategies: StrategyInfo[];
  regimes: string[];
  regime_labels: Record<string, string>;
  health_help: Record<string, string>;
  discovery_help: Record<string, string>;
};

export type QuantConfig = {
  engine_enabled: boolean;
  min_final_score: number;
  min_confluence_level: string;
  single_signal_min_score: number;
  weights: Record<string, number>;
  max_correlation: number;
  max_open_per_strategy: number;
  conflict_margin: number;
  breakeven_at_r: number;
  exit_on_regime_change: boolean;
  exit_on_momentum_loss: boolean;
  exit_on_opposite_signal: boolean;
  auto_disable: boolean;
  health_min_trades: number;
  research_top_n: number;
  research_days: number;
  research_days_15m: number;
  research_days_5m: number;
  timeframes: string[] | null;
  momentum_exit_timeframes: string[] | null;
  block_hours_enabled: boolean;
  block_start_hour: number;
  block_end_hour: number;
  block_timezone: string;
};

export type RiskStatus = {
  risk_paused: string | null;
  blocked_hours: string | null;
  risk_reset_at: string | null;
  block: { enabled: boolean; start: number; end: number; timezone: string };
};

export type CycleSummary = {
  timeframe: string; candle_time: number; btc_regime: string; universe: number; analyzed: number; errors: number;
  signals: Record<string, number>; opportunities: number; selected: number; executed: string[];
  exits: { symbol: string; reason: string }[]; engine_ready: boolean; risk_paused: string | null; finished_at: string;
};

export type Overview = {
  safety: Safety;
  data: DataInfo;
  engine_enabled: boolean;
  regime: ({ candle_time: number; btc_regime: string; market_state: string; label: string; details: Record<string, unknown>; created_at: string }) | null;
  regime_history: { candle_time: number; btc_regime: string; changed: boolean }[];
  regime_labels: Record<string, string>;
  last_cycles: Record<string, CycleSummary | null>;
  timeframes: string[];
  open_trades: TradeBrief[];
  recent_trades: TradeBrief[];
  totals: { closed: number; pnl: number; r: number };
  top_opportunities: Opportunity[];
  research: Record<string, { id: number; status: string; progress: string | null; finished_at: string | null; summary: Record<string, unknown> | null } | null>;
};

// ---------------------------------------------------------------- rótulos
export const REGIME_SHORT: Record<string, string> = {
  BTC_BULL_TREND: "Alta",
  BTC_BEAR_TREND: "Baixa",
  BTC_RANGE: "Lateral",
  BTC_VOLATILITY_EXPANSION: "Vol. expansão",
  BTC_VOLATILITY_CONTRACTION: "Vol. comprimida",
  BTC_TRANSITION: "Transição",
};

export const REGIME_COLOR: Record<string, string> = {
  BTC_BULL_TREND: "var(--green)",
  BTC_BEAR_TREND: "var(--red)",
  BTC_RANGE: "var(--blue)",
  BTC_VOLATILITY_EXPANSION: "var(--orange)",
  BTC_VOLATILITY_CONTRACTION: "#a78bfa",
  BTC_TRANSITION: "var(--muted)",
};

export const LEVEL_LABEL: Record<string, string> = {
  SINGLE_SIGNAL: "Sinal único",
  CONFLUENCE: "Confluência",
  STRONG_CONFLUENCE: "Confluência forte",
};

export const OPP_STATUS: Record<string, { label: string; color: string }> = {
  RANKED: { label: "Selecionada", color: "blue" },
  EXECUTED: { label: "Executada", color: "green" },
  SKIPPED: { label: "Descartada", color: "" },
  CONFLICT: { label: "Conflito", color: "orange" },
  FAILED: { label: "Falhou", color: "red" },
  EXPIRED: { label: "Expirada", color: "" },
};

export const SIGNAL_STATUS: Record<string, { label: string; help: string; color: string }> = {
  ACCEPTED: { label: "Aceito", help: "passou nos filtros da estratégia e foi para a confluência", color: "green" },
  USED: { label: "Usado em trade", help: "fez parte de uma oportunidade executada", color: "blue" },
  BELOW_MIN: { label: "Score baixo", help: "abaixo do score mínimo configurado para a estratégia", color: "" },
  REGIME_BLOCKED: { label: "Regime bloqueado", help: "regime do BTC não aceito pela estratégia ou filtro do BTC", color: "orange" },
  DIRECTION_DISABLED: { label: "Direção desligada", help: "LONG ou SHORT desativado para a estratégia", color: "" },
  COOLDOWN: { label: "Cooldown", help: "a estratégia operou este par há pouco tempo", color: "" },
  DAILY_LIMIT: { label: "Limite diário", help: "limite de trades por dia da estratégia atingido", color: "" },
  EXPIRED: { label: "Expirado", help: "candle seguinte já fechou", color: "" },
};

export const EXIT_LABEL: Record<string, string> = {
  STOP: "Stop",
  TARGET: "Alvo",
  TIME_EXIT: "Tempo máximo",
  REGIME_CHANGE: "Mudança de regime",
  MOMENTUM_LOSS: "Perda de momentum",
  EXIT_SIGNAL: "Sinal oposto",
  MANUAL_DEMO_EXIT: "Manual (DEMO)",
  EXTERNAL_CLOSE: "Fechada fora do motor",
  FAILSAFE: "Failsafe (sem stop)",
};

export const HEALTH_COLOR: Record<string, string> = { ACTIVE: "green", WATCH: "orange", DEGRADED: "red", DISABLED: "" };
export const HEALTH_LABEL: Record<string, string> = { ACTIVE: "Ativa", WATCH: "Em observação", DEGRADED: "Degradada", DISABLED: "Desativada" };

export const TAG_COLOR: Record<string, string> = {
  BEST: "green", PROMISING: "blue", WEAK: "red", REGIME_DEPENDENT: "orange",
  LONG_ONLY_STRONG: "green", SHORT_ONLY_STRONG: "green", UNVALIDATED: "",
};
export const TAG_LABEL: Record<string, string> = {
  BEST: "Robusta no histórico", PROMISING: "Promissora", WEAK: "Fraca", REGIME_DEPENDENT: "Depende do regime",
  LONG_ONLY_STRONG: "Boa só em LONG", SHORT_ONLY_STRONG: "Boa só em SHORT", UNVALIDATED: "Não validada",
};

export const FAMILY_LABEL: Record<string, string> = {
  trend_following: "Tendência", momentum: "Momentum", volatility: "Volatilidade", structure: "Estrutura",
  mean_reversion: "Reversão à média", microstructure: "Microestrutura", regime: "Regime (filtro)",
};

export const COMPONENT_LABEL: Record<string, string> = {
  signal: "Sinal (primária)", regime: "Regime BTC", trend: "Tendência", volatility: "Volatilidade",
  volume: "Volume", structure: "Estrutura", momentum: "Momentum", confluence: "Confluência", historical: "Histórico",
};

export function tfOf(strategyId: string): string {
  return strategyId.endsWith("_15M") ? "15m" : strategyId.endsWith("_5M") ? "5m" : "1h";
}

export function fmtR(r: number | null | undefined, digits = 2): string {
  if (r === null || r === undefined || Number.isNaN(r)) return "-";
  return `${r > 0 ? "+" : ""}${r.toFixed(digits)}R`;
}

export function cls(n: number | null | undefined): string {
  if (n === null || n === undefined) return "";
  return n > 0 ? "pos" : n < 0 ? "neg" : "";
}

export function fmtCandle(ms: number | null | undefined): string {
  if (!ms) return "-";
  return new Date(ms).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export function fmtDuration(min: number | null | undefined): string {
  if (min === null || min === undefined) return "-";
  if (min < 60) return `${Math.round(min)} min`;
  const h = Math.floor(min / 60);
  return h < 48 ? `${h}h${String(Math.round(min % 60)).padStart(2, "0")}` : `${(h / 24).toFixed(1)} d`;
}
