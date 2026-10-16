"""Indicadores técnicos (numpy puro, sem dependências extras)."""

import numpy as np


def _first_valid(values: np.ndarray) -> int | None:
    idx = np.where(~np.isnan(values))[0]
    return int(idx[0]) if len(idx) else None


def ema(values: np.ndarray, period: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    start = _first_valid(values)
    if start is None or len(values) - start < period:
        return out
    alpha = 2.0 / (period + 1)
    seed = start + period - 1
    out[seed] = values[start:seed + 1].mean()
    for i in range(seed + 1, len(values)):
        out[i] = values[i] * alpha + out[i - 1] * (1 - alpha)
    return out


def rma(values: np.ndarray, period: int) -> np.ndarray:
    """Média móvel de Wilder."""
    values = np.asarray(values, dtype=float)
    out = np.full(len(values), np.nan)
    start = _first_valid(values)
    if start is None or len(values) - start < period:
        return out
    seed = start + period - 1
    out[seed] = values[start:seed + 1].mean()
    for i in range(seed + 1, len(values)):
        out[i] = (out[i - 1] * (period - 1) + values[i]) / period
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    delta = np.diff(close, prepend=np.nan)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    gain[0] = loss[0] = np.nan
    avg_gain, avg_loss = rma(gain, period), rma(loss, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + avg_gain / avg_loss)
    return np.where((avg_loss == 0) & ~np.isnan(avg_gain), 100.0, out)


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev_close = np.roll(close, 1)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    return tr.astype(float)


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    return rma(true_range(high, low, close), period)


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14):
    up = high - np.roll(high, 1)
    down = np.roll(low, 1) - low
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(high, low, close)
    plus_dm[0] = minus_dm[0] = tr[0] = np.nan
    atr_ = rma(tr, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100 * rma(plus_dm, period) / atr_
        minus_di = 100 * rma(minus_dm, period) / atr_
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    dx = np.where(np.isinf(dx), np.nan, dx)
    return rma(dx, period), plus_di, minus_di


def macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(close: np.ndarray, period: int = 20, mult: float = 2.0):
    mid = np.full(len(close), np.nan)
    upper = np.full(len(close), np.nan)
    lower = np.full(len(close), np.nan)
    for i in range(period - 1, len(close)):
        window = close[i - period + 1:i + 1]
        m, s = window.mean(), window.std()
        mid[i], upper[i], lower[i] = m, m + mult * s, m - mult * s
    return upper, mid, lower


def swing_levels(high: np.ndarray, low: np.ndarray, price: float, window: int = 3, lookback: int = 100):
    """Suporte/Resistência pelos swing points (pivôs) mais próximos do preço."""
    n = len(high)
    highs, lows = [], []
    for i in range(max(window, n - lookback), n - window):
        if high[i] == high[i - window:i + window + 1].max():
            highs.append(float(high[i]))
        if low[i] == low[i - window:i + window + 1].min():
            lows.append(float(low[i]))
    resistance = min((h for h in highs if h > price), default=None)
    support = max((l for l in lows if l < price), default=None)
    return support, resistance


def slope_pct(series: np.ndarray, lookback: int = 5) -> float:
    """Variação percentual da série nos últimos `lookback` candles."""
    a, b = series[-1 - lookback], series[-1]
    if np.isnan(a) or np.isnan(b) or a == 0:
        return float("nan")
    return float((b - a) / a * 100)
