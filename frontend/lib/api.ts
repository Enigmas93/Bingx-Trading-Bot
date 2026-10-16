export type JobInfo = {
  running: boolean;
  last_run: string | null;
  last_result: unknown;
  next_run: string | null;
};

export type Status = {
  mode: "demo" | "live";
  automation_enabled: boolean;
  scheduler_running: boolean;
  news_llm_configured: boolean;
  balance: { asset: string; balance: number; equity: number; available: number; used_margin: number; unrealized_pnl: number } | null;
  balance_error: string | null;
  open_positions: number;
  approved_signals: number;
  risk: RiskState;
  jobs: Record<string, JobInfo>;
};

export type Config = {
  automation_enabled: boolean;
  max_positions: number;
  min_execute_score: number;
  leverage: number;
  capital_percent: number;
  news_check_enabled: boolean;
  timeframe: string;
  scan_top_n: number;
  min_volume_usdt: number;
  max_spread_pct: number;
  max_funding_pct: number;
  block_hours_enabled: boolean;
  block_hour_start: number;
  block_hour_end: number;
  signal_expiry_minutes: number;
  market_data_live: boolean;
  min_breadth: number;
  min_adx: number;
  htf_filter_enabled: boolean;
  htf_timeframe: string;
  btc_filter_enabled: boolean;
  require_confirmation_candle: boolean;
  min_ema50_slope_pct: number;
  min_stop_cost_ratio: number;
  fee_rate_pct: number;
  structural_stop: boolean;
  max_stop_atr: number;
  breakeven_after_tp1: boolean;
  sizing_mode: string;
  risk_per_trade_pct: number;
  symbol_cooldown_minutes: number;
  max_same_direction: number;
  daily_loss_limit_pct: number;
  max_consecutive_losses: number;
  news_top_candidates: number;
  news_block_generic: boolean;
};

export type RiskState = {
  paused: boolean;
  reason: string | null;
  daily_pnl: number;
  consecutive_losses: number;
  trades_today: number;
  daily_loss_limit?: number;
};

export type Stats = {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  net_pnl: number;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  avg_r: number | null;
};

export type GroupStats = Stats & { group: string };

export type Performance = {
  summary: Stats & { max_drawdown: number; confirmed: number; estimated: number };
  equity_curve: { t: string; equity: number }[];
  by_direction: GroupStats[];
  by_exit_reason: GroupStats[];
  by_score: GroupStats[];
  by_adx: GroupStats[];
  by_news: GroupStats[];
  by_symbol: GroupStats[];
  news_shadow: Record<string, number>;
};

export type BacktestGroup = { group: string; trades: number; win_rate: number; total_r: number; avg_r: number };

export type BacktestSummary = {
  trades: number;
  trades_per_day?: number;
  filtro?: string;
  sem_limites?: { trades: number; win_rate: number | null; profit_factor: number | null; total_r: number; return_pct: number; max_drawdown_pct: number };
  win_rate: number | null;
  profit_factor: number | null;
  expectancy_r: number | null;
  total_r: number;
  max_drawdown_r: number;
  return_pct: number;
  max_drawdown_pct: number;
  by_direction: BacktestGroup[];
  by_exit_reason: BacktestGroup[];
  by_symbol: BacktestGroup[];
};

export type BacktestTrade = {
  symbol: string;
  direction: string;
  entry_time: number;
  exit_time: number;
  score: number;
  adx: number;
  r: number;
  exit_reason: string;
};

export type BacktestRun = {
  id: number;
  status: "running" | "done" | "error";
  params: Record<string, unknown>;
  progress: string | null;
  summary: BacktestSummary | null;
  trades?: BacktestTrade[] | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
};

export type Signal = {
  id: number;
  symbol: string;
  direction: "LONG" | "SHORT";
  status: string;
  confidence_score: number;
  min_score_required: number;
  adx: number;
  atr: number;
  entry_price: number;
  stop_loss: number;
  tp1: number;
  tp2: number;
  tp3: number;
  indicators: Record<string, number | null>;
  score_breakdown: Record<string, number>;
  news_result: { impact: string; summary: string; score_adjustment: number } | null;
  status_reason: string | null;
  created_at: string;
};

export type Position = {
  id: number;
  symbol: string;
  direction: "LONG" | "SHORT";
  quantity: number;
  entry_price: number;
  current_price: number | null;
  stop_loss: number | null;
  tp1: number | null;
  tp2: number | null;
  tp3: number | null;
  unrealized_pnl: number;
  realized_pnl: number | null;
  margin: number | null;
  leverage: number | null;
  protected: boolean;
  source: string;
  status: string;
  opened_at: string;
  closed_at: string | null;
  gross_pnl: number | null;
  fees: number | null;
  funding: number | null;
  pnl_confirmed: boolean;
  exit_reason: string | null;
  tp1_hit: boolean;
  breakeven_set: boolean;
  risk_amount: number | null;
};

export type Trade = {
  id: number;
  signal_id: number | null;
  symbol: string;
  direction: string;
  quantity: number;
  entry_price: number;
  leverage: number;
  stop_loss: number;
  tp1: number;
  tp2: number;
  tp3: number;
  entry_order_id: string | null;
  sl_order_id: string | null;
  tp1_order_id: string | null;
  tp2_order_id: string | null;
  tp3_order_id: string | null;
  status: string;
  errors: string | null;
  created_at: string;
  closed_at: string | null;
};

export type LogEntry = { id: number; level: string; module: string; message: string; created_at: string };

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

export function fmt(n: number | null | undefined, digits = 4): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "-";
  return Number(n).toLocaleString("pt-BR", { maximumFractionDigits: digits });
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return d.toLocaleString("pt-BR");
}
