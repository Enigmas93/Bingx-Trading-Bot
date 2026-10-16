"""Features compartilhadas por todas as estratégias.

Calculadas UMA vez por par/timeframe sobre a série inteira de candles FECHADOS, de forma causal (o valor no
índice i usa apenas candles <= i). As janelas "prev" excluem o candle atual (ex.: máxima dos 20 anteriores).
"""

import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

from .. import indicators as ind

PIVOT_WINDOW = 3


def _prev_window(arr: np.ndarray, n: int, fn) -> np.ndarray:
    """out[i] = fn(arr[i-n:i]) — janela dos n candles ANTERIORES (exclui i)."""
    out = np.full(len(arr), np.nan)
    if len(arr) > n:
        out[n:] = fn(swv(arr[:-1], n), axis=1)
    return out


def _prev_pct_rank(arr: np.ndarray, n: int) -> np.ndarray:
    """Fração dos n valores anteriores que são menores que o valor atual (0..1)."""
    out = np.full(len(arr), np.nan)
    if len(arr) > n:
        w = swv(arr[:-1], n)
        cur = arr[n:][:, None]
        valid = ~np.isnan(w)
        out[n:] = np.where(valid.sum(axis=1) > 0, (w < cur).sum(axis=1) / np.maximum(valid.sum(axis=1), 1), np.nan)
    return out


def _shift(arr: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if len(arr) > k:
        out[k:] = arr[:-k]
    return out


def _swings(high: np.ndarray, low: np.ndarray, w: int = PIVOT_WINDOW):
    """Pivôs confirmados: o pivô em j só passa a existir em j+w (sem olhar o futuro)."""
    n = len(high)
    ph = np.zeros(n, dtype=bool); pl = np.zeros(n, dtype=bool)
    if n >= 2 * w + 1:
        hw, lw = swv(high, 2 * w + 1), swv(low, 2 * w + 1)
        ph[w:n - w] = high[w:n - w] == hw.max(axis=1)
        pl[w:n - w] = low[w:n - w] == lw.min(axis=1)
    last_sh = np.full(n, np.nan); prev_sh = np.full(n, np.nan); last_sh_i = np.full(n, np.nan)
    last_sl = np.full(n, np.nan); prev_sl = np.full(n, np.nan); last_sl_i = np.full(n, np.nan)
    cur_h = cur_hp = cur_l = cur_lp = math.nan
    cur_hi = cur_li = math.nan
    for i in range(n):
        j = i - w
        if j >= 0 and ph[j]:
            cur_hp, cur_h, cur_hi = cur_h, high[j], j
        if j >= 0 and pl[j]:
            cur_lp, cur_l, cur_li = cur_l, low[j], j
        last_sh[i], prev_sh[i], last_sh_i[i] = cur_h, cur_hp, cur_hi
        last_sl[i], prev_sl[i], last_sl_i[i] = cur_l, cur_lp, cur_li
    idx = np.arange(n)
    return last_sh, prev_sh, idx - last_sh_i, last_sl, prev_sl, idx - last_sl_i


def compute(candles: list[dict]) -> dict:
    o = np.array([k["open"] for k in candles], dtype=float)
    h = np.array([k["high"] for k in candles], dtype=float)
    l = np.array([k["low"] for k in candles], dtype=float)
    c = np.array([k["close"] for k in candles], dtype=float)
    v = np.array([k["volume"] for k in candles], dtype=float)
    t = np.array([k["time"] for k in candles], dtype=np.int64)
    f = {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}
    f["ema9"], f["ema21"], f["ema50"] = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)
    f["ema100"] = ind.ema(c, 100)
    e50_5 = _shift(f["ema50"], 5)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["slope50"] = (f["ema50"] - e50_5) / e50_5 * 100
    f["rsi"] = ind.rsi(c, 14)
    f["atr"] = ind.atr(h, l, c, 14)
    f["atr_pct"] = f["atr"] / c * 100
    f["adx"], f["pdi"], f["mdi"] = ind.adx(h, l, c, 14)
    f["adx_slope3"] = f["adx"] - _shift(f["adx"], 3)
    f["macd"], f["macd_sig"], f["macd_hist"] = ind.macd(c)
    f["bb_up"], f["bb_mid"], f["bb_low"] = ind.bollinger(c, 20, 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["bb_width"] = (f["bb_up"] - f["bb_low"]) / f["bb_mid"] * 100
        f["pct_b"] = (c - f["bb_low"]) / (f["bb_up"] - f["bb_low"])
    f["vol_ma20"] = _prev_window(v, 20, np.mean)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["vol_ratio"] = v / f["vol_ma20"]
    f["don_high20"] = _prev_window(h, 20, np.max)
    f["don_low20"] = _prev_window(l, 20, np.min)
    f["bb_width_pr120"] = _prev_pct_rank(f["bb_width"], 120)
    f["atr_pct_pr120"] = _prev_pct_rank(f["atr_pct"], 120)
    f["atr_pct_ma20"] = _prev_window(f["atr_pct"], 20, np.mean)
    f["bb_width_pr_min10"] = _prev_window(np.nan_to_num(f["bb_width_pr120"], nan=1.0), 10, np.min)
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        f["body_ratio"] = np.where(rng > 0, np.abs(c - o) / rng, 0.0)
        f["upper_wick"] = np.where(rng > 0, (h - np.maximum(o, c)) / rng, 0.0)
        f["lower_wick"] = np.where(rng > 0, (np.minimum(o, c) - l) / rng, 0.0)
        f["ret3"] = c / _shift(c, 3) - 1
    (f["last_sh"], f["prev_sh"], f["last_sh_age"], f["last_sl"], f["prev_sl"], f["last_sl_age"]) = _swings(h, l)
    return f


SNAPSHOT_KEYS = ("close", "open", "high", "low", "volume", "ema9", "ema21", "ema50", "ema100", "slope50", "rsi", "atr",
                 "atr_pct", "adx", "pdi", "mdi", "adx_slope3", "macd", "macd_sig", "macd_hist", "bb_up", "bb_mid",
                 "bb_low", "bb_width", "pct_b", "vol_ratio", "don_high20", "don_low20", "bb_width_pr120",
                 "atr_pct_pr120", "atr_pct_ma20", "bb_width_pr_min10", "body_ratio", "upper_wick", "lower_wick",
                 "ret3", "last_sh", "prev_sh", "last_sh_age", "last_sl", "prev_sl", "last_sl_age")


class FeatureView:
    """Acesso às features no candle i (o último fechado no ao vivo; qualquer candle no backtest)."""

    def __init__(self, feats: dict, i: int, candles: list[dict] | None = None):
        self.f = feats
        self.i = i
        self.candles = candles

    def __getitem__(self, key: str) -> float:
        return float(self.f[key][self.i])

    def prev(self, key: str, k: int = 1) -> float:
        j = self.i - k
        return float(self.f[key][j]) if j >= 0 else math.nan

    @property
    def time(self) -> int:
        return int(self.f["time"][self.i])

    def ok(self, *keys: str) -> bool:
        return all(not math.isnan(self[k]) for k in keys)

    def snapshot(self) -> dict:
        out = {}
        for k in SNAPSHOT_KEYS:
            if k in self.f:
                x = float(self.f[k][self.i])
                out[k] = None if math.isnan(x) else round(x, 8)
        out["candle_time"] = self.time
        return out

    def window(self, n: int) -> list[dict]:
        """Os n candles fechados até i (inclusive) — usado por estratégias que reaproveitam lógica legada."""
        return self.candles[max(0, self.i - n + 1):self.i + 1] if self.candles else []
