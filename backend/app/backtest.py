"""Backtest da estratégia com candles históricos reais da BingX (mesma lógica do scanner ao vivo).

Simplificações: cada par é simulado de forma independente (sem limite global de posições), entrada na
abertura do candle seguinte ao sinal, e se SL e TP caem no mesmo candle conta o SL (conservador).
Resultados em R (múltiplos do risco), já descontando taxas e spread.
"""

import bisect
import threading
import time

from . import botlog
from .bingx import BingXClient
from .common import get_config
from .database import session_scope
from .models import BacktestRun, utcnow
from .scanner import NON_CRYPTO
from .strategy import StrategyParams, analyze, compute_levels, cost_filter, regime_filter, trend_of

MODULE = "backtest"
WINDOW = 200
HTF_WINDOW = 120
INTERVAL_MS = {"1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
               "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000}
TP_FRACTIONS = {"tp1": 0.5, "tp2": 0.5 * 40 / 70, "tp3": 0.5 * 30 / 70}

_lock = threading.Lock()
_market = BingXClient("", "", demo=False)  # dados públicos da conta real (histórico de mercado verdadeiro)


def _fetch_history(symbol: str, interval: str, start_ms: int, end_ms: int) -> list[dict]:
    candles: dict[int, dict] = {}
    cursor = end_ms
    for _ in range(200):
        page = _market.get_klines(symbol, interval, 1000, end_ms=cursor)
        if not page:
            break
        for k in page:
            candles[k["time"]] = k
        oldest = page[0]["time"]
        if oldest <= start_ms or len(page) < 2:
            break
        cursor = oldest - 1
    return [candles[t] for t in sorted(candles) if t >= start_ms]


def _trend_series(candles: list[dict], interval_ms: int) -> tuple[list[int], list[str]]:
    """Tendência do timeframe maior conhecida no fechamento de cada candle HTF."""
    times, trends = [], []
    for i in range(60, len(candles)):
        times.append(candles[i]["time"] + interval_ms)
        trends.append(trend_of(candles[max(0, i - HTF_WINDOW + 1):i + 1]))
    return times, trends


def _trend_at(series: tuple[list[int], list[str]] | None, t_ms: int) -> str | None:
    if not series or not series[0]:
        return None
    idx = bisect.bisect_right(series[0], t_ms) - 1
    return series[1][idx] if idx >= 0 else None


def _simulate(symbol: str, candles: list[dict], ltf_ms: int, htf, btc, params: StrategyParams, opts: dict) -> list[dict]:
    fee = params.fee_rate_pct / 100
    half_spread = opts["spread_pct"] / 200
    cooldown_bars = int(opts["cooldown_minutes"] * 60_000 / ltf_ms)
    trades: list[dict] = []
    pos = None
    cooldown_until = -1

    def close_part(name: str, price: float, frac: float):
        sign = 1 if pos["direction"] == "LONG" else -1
        gross = frac * sign * (price - pos["entry"]) / pos["risk"]
        cost = frac * (pos["entry"] + price) * fee / pos["risk"]
        pos["r"] += gross - cost
        pos["left"] -= frac
        pos["fills"].append(name)

    for i in range(WINDOW, len(candles)):
        k = candles[i]
        if pos is not None:
            is_long = pos["direction"] == "LONG"
            sl_hit = k["low"] <= pos["sl"] if is_long else k["high"] >= pos["sl"]
            if sl_hit:
                exit_px = pos["sl"] * (1 - half_spread if is_long else 1 + half_spread)
                close_part("BE" if pos["be"] else "SL", exit_px, pos["left"])
            else:
                for name in ("tp1", "tp2", "tp3"):
                    if name in pos["done"]:
                        continue
                    tp = pos[name]
                    if (k["high"] >= tp) if is_long else (k["low"] <= tp):
                        close_part(name.upper(), tp, TP_FRACTIONS[name])
                        pos["done"].add(name)
                        if name == "tp1" and opts["breakeven"]:
                            buf = pos["entry"] * 2 * fee
                            pos["sl"] = pos["entry"] + buf if is_long else pos["entry"] - buf
                            pos["be"] = True
                    else:
                        break
            if pos["left"] <= 1e-9 or i == len(candles) - 1:
                if pos["left"] > 1e-9:
                    close_part("FIM", k["close"], pos["left"])
                trades.append({
                    "symbol": symbol, "direction": pos["direction"], "entry_time": pos["entry_time"],
                    "exit_time": k["time"], "score": pos["score"], "adx": pos["adx"],
                    "r": round(pos["r"], 4), "exit_reason": "+".join(pos["fills"]),
                })
                if pos["r"] < 0:
                    cooldown_until = i + cooldown_bars
                pos = None
            continue

        if i <= cooldown_until or i + 1 >= len(candles):
            continue
        res = analyze(candles[i - WINDOW + 1:i + 1], params)
        if not res["passed"] or res["score"] < opts["min_execute_score"]:
            continue
        close_time = k["time"] + ltf_ms
        direction = res["direction"]
        if regime_filter(direction, _trend_at(htf, close_time), _trend_at(btc, close_time),
                         symbol == "BTC-USDT", params):
            continue
        nxt = candles[i + 1]
        entry = nxt["open"] * (1 + half_spread if direction == "LONG" else 1 - half_spread)
        snap = res["indicators"]
        levels = compute_levels(direction, entry, snap["atr"], snap["support"], snap["resistance"],
                                res["pullback_extreme"], params)
        if levels is None or cost_filter(entry, levels["stop_loss"], opts["spread_pct"], params):
            continue
        pos = {"direction": direction, "entry": entry, "sl": levels["stop_loss"], "tp1": levels["tp1"],
               "tp2": levels["tp2"], "tp3": levels["tp3"], "risk": abs(entry - levels["stop_loss"]),
               "left": 1.0, "r": 0.0, "fills": [], "done": set(), "be": False,
               "entry_time": nxt["time"], "score": res["score"], "adx": snap["adx"]}
    return trades


def _apply_portfolio(trades: list[dict], max_positions: int, min_breadth: int) -> list[dict]:
    """Aplica as regras de carteira do bot ao vivo: amplitude mínima por candle e limite de posições.

    Amplitude = nº de pares que entraram no mesmo candle. Entre sinais do mesmo candle, prioriza o maior score.
    """
    by_entry: dict[int, list[dict]] = {}
    for t in trades:
        by_entry.setdefault(t["entry_time"], []).append(t)
    open_until: list[int] = []
    taken: list[dict] = []
    for ts in sorted(by_entry):
        group = by_entry[ts]
        if len(group) < min_breadth:
            continue
        open_until = [x for x in open_until if x > ts]
        for t in sorted(group, key=lambda x: -x["score"]):
            if len(open_until) >= max_positions:
                break
            open_until.append(t["exit_time"])
            taken.append({**t, "breadth": len(group)})
    return taken


def _summarize(trades: list[dict], risk_pct: float) -> dict:
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    equity, peak, max_dd = 1.0, 1.0, 0.0
    total_r, peak_r, max_dd_r = 0.0, 0.0, 0.0
    for t in sorted(trades, key=lambda x: x["exit_time"]):
        equity *= 1 + t["r"] * risk_pct / 100
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)
        total_r += t["r"]
        peak_r = max(peak_r, total_r)
        max_dd_r = max(max_dd_r, peak_r - total_r)

    def group(key) -> list[dict]:
        out: dict[str, list[float]] = {}
        for t in trades:
            out.setdefault(key(t), []).append(t["r"])
        return sorted(({"group": g, "trades": len(v), "win_rate": round(sum(1 for r in v if r > 0) / len(v) * 100, 1),
                        "total_r": round(sum(v), 2), "avg_r": round(sum(v) / len(v), 3)} for g, v in out.items()),
                      key=lambda x: -x["trades"])

    return {
        "trades": len(rs),
        "trades_per_day": round(len(rs) / max(1e-9, (max(t["exit_time"] for t in trades)
                                                      - min(t["entry_time"] for t in trades)) / 86_400_000), 2)
        if trades else 0,
        "win_rate": round(len(wins) / len(rs) * 100, 1) if rs else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
        "expectancy_r": round(sum(rs) / len(rs), 3) if rs else None,
        "total_r": round(total_r, 2),
        "max_drawdown_r": round(max_dd_r, 2),
        "return_pct": round((equity - 1) * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "by_direction": group(lambda t: t["direction"]),
        "by_exit_reason": group(lambda t: t["exit_reason"]),
        "by_symbol": group(lambda t: t["symbol"]),
    }


def _run(run_id: int, opts: dict) -> None:
    cfg = get_config()
    params = StrategyParams.from_config(cfg)
    for key in StrategyParams.__dataclass_fields__:
        if key in opts:
            setattr(params, key, opts[key])
    ltf, htf_tf = opts["timeframe"], cfg.htf_timeframe
    ltf_ms, htf_ms = INTERVAL_MS[ltf], INTERVAL_MS[htf_tf]
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - opts["days"] * 86_400_000
    warmup_start = start_ms - WINDOW * ltf_ms
    htf_start = start_ms - HTF_WINDOW * htf_ms * 2

    def progress(text: str):
        with session_scope() as db:
            db.get(BacktestRun, run_id).progress = text

    try:
        symbols = opts.get("symbols") or []
        if not symbols:
            tickers = [t for t in _market.get_tickers()
                       if str(t.get("symbol", "")).endswith("-USDT") and not NON_CRYPTO.match(t["symbol"])]
            tickers.sort(key=lambda t: float(t.get("quoteVolume") or 0), reverse=True)
            symbols = [t["symbol"] for t in tickers[: opts["top_n"]]]

        btc = None
        if params.btc_filter_enabled:
            btc = _trend_series(_fetch_history("BTC-USDT", htf_tf, htf_start, end_ms), htf_ms)

        all_trades: list[dict] = []
        for n, symbol in enumerate(symbols, 1):
            progress(f"{n}/{len(symbols)}: {symbol} ({len(all_trades)} trades até agora)")
            try:
                candles = _fetch_history(symbol, ltf, warmup_start, end_ms)
                htf = _trend_series(_fetch_history(symbol, htf_tf, htf_start, end_ms), htf_ms) \
                    if params.htf_filter_enabled else None
                all_trades.extend(_simulate(symbol, candles, ltf_ms, htf, btc, params, opts))
            except Exception as exc:
                botlog.warn(MODULE, f"Backtest {symbol}: {exc}")

        portfolio = _apply_portfolio(all_trades, opts["max_positions"], opts["min_breadth"])
        summary = _summarize(portfolio, opts["risk_pct"])
        raw = _summarize(all_trades, opts["risk_pct"])
        summary["sem_limites"] = {k: v for k, v in raw.items() if not k.startswith("by_")}
        summary["filtro"] = (f"máx {opts['max_positions']} posições simultâneas, amplitude mínima "
                             f"{opts['min_breadth']} par(es) por candle")
        with session_scope() as db:
            run = db.get(BacktestRun, run_id)
            run.status, run.summary, run.finished_at = "done", summary, utcnow()
            run.trades = sorted(portfolio, key=lambda t: t["entry_time"])
            run.progress = f"Concluído: {len(symbols)} pares, {len(all_trades)} trades"
        botlog.info(MODULE, f"Backtest #{run_id} concluído: {summary['trades']} trades, "
                            f"acerto {summary['win_rate']}%, PF {summary['profit_factor']}, total {summary['total_r']}R")
    except Exception as exc:
        with session_scope() as db:
            run = db.get(BacktestRun, run_id)
            run.status, run.error, run.finished_at = "error", str(exc), utcnow()
        botlog.error(MODULE, f"Backtest #{run_id} falhou: {exc}")
    finally:
        _lock.release()


def start_backtest(opts: dict) -> int:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Já existe um backtest em andamento")
    cfg = get_config()
    defaults = {"timeframe": cfg.timeframe, "days": 30, "top_n": cfg.scan_top_n, "symbols": [], "spread_pct": 0.05,
                "risk_pct": cfg.risk_per_trade_pct, "breakeven": cfg.breakeven_after_tp1,
                "cooldown_minutes": cfg.symbol_cooldown_minutes, "min_execute_score": cfg.min_execute_score,
                "max_positions": cfg.max_positions, "min_breadth": cfg.min_breadth}
    opts = {**defaults, **{k: v for k, v in opts.items() if v is not None}}
    if opts["timeframe"] not in INTERVAL_MS:
        _lock.release()
        raise ValueError(f"Timeframe inválido: {opts['timeframe']}")
    try:
        with session_scope() as db:
            run = BacktestRun(params=opts, progress="Iniciando...")
            db.add(run)
            db.flush()
            run_id = run.id
    except Exception:
        _lock.release()
        raise
    threading.Thread(target=_run, args=(run_id, opts), daemon=True).start()
    return run_id
