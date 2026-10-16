"""STRATEGY_01 — Trend EMA: segue tendência estabelecida (médias alinhadas, inclinação e força)."""

from ..base import Strategy, clamp01


class TrendEMA(Strategy):
    strategy_id = "TREND_EMA"
    name = "Trend EMA"
    version = "1.0"
    family = "trend_following"
    default_regimes = ("BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_VOLATILITY_EXPANSION")
    default_min_score = 65
    max_hold_bars = 48
    exit_style = "trend"
    description = ("Entra a favor de uma tendência já estabelecida: EMA9 > EMA21 > EMA50 (LONG) com EMA50 inclinada, "
                   "ADX mínimo e preço ainda próximo da EMA21 (não esticado). Stop além da EMA50; alvo em múltiplos do risco.")
    feature_keys = ("close", "ema9", "ema21", "ema50", "slope50", "adx", "atr", "rsi")
    long_params = {"min_slope_pct": 0.03, "min_adx": 20.0, "max_dist_ema21_atr": 1.5, "stop_buffer_atr": 0.3,
                   "min_stop_atr": 1.2, "target_r": 2.0}
    short_params = {"min_slope_pct": 0.04, "min_adx": 22.0, "max_dist_ema21_atr": 1.5, "stop_buffer_atr": 0.3,
                    "min_stop_atr": 1.2, "target_r": 2.0}

    def evaluate_long(self, fv, ctx):
        p = self.long_params
        c, e9, e21, e50, s, adx, atr = (fv[k] for k in ("close", "ema9", "ema21", "ema50", "slope50", "adx", "atr"))
        dist = (c - e21) / atr
        conds = [
            ("EMA9 > EMA21 > EMA50", e9 > e21 > e50),
            (f"inclinação da EMA50 ≥ {p['min_slope_pct']}% ({s:.3f}%)", s >= p["min_slope_pct"]),
            (f"ADX ≥ {p['min_adx']:.0f} ({adx:.1f})", adx >= p["min_adx"]),
            ("preço acima da EMA21", c > e21),
            (f"preço a no máx. {p['max_dist_ema21_atr']} ATR da EMA21 ({dist:.2f})", dist <= p["max_dist_ema21_atr"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        score = 45 + 20 * clamp01(s / 0.15) + 20 * clamp01((adx - p["min_adx"]) / 20) + 15 * clamp01(1 - dist / p["max_dist_ema21_atr"])
        risk = max(p["min_stop_atr"] * atr, c - e50 + p["stop_buffer_atr"] * atr)
        return self._make(fv, "LONG", score, conds, c - risk, c + p["target_r"] * risk,
                          "Tendência de alta estabelecida com EMAs alinhadas, inclinação e ADX confirmando.")

    def evaluate_short(self, fv, ctx):
        p = self.short_params
        c, e9, e21, e50, s, adx, atr = (fv[k] for k in ("close", "ema9", "ema21", "ema50", "slope50", "adx", "atr"))
        dist = (e21 - c) / atr
        conds = [
            ("EMA9 < EMA21 < EMA50", e9 < e21 < e50),
            (f"inclinação da EMA50 ≤ −{p['min_slope_pct']}% ({s:.3f}%)", s <= -p["min_slope_pct"]),
            (f"ADX ≥ {p['min_adx']:.0f} ({adx:.1f})", adx >= p["min_adx"]),
            ("preço abaixo da EMA21", c < e21),
            (f"preço a no máx. {p['max_dist_ema21_atr']} ATR da EMA21 ({dist:.2f})", dist <= p["max_dist_ema21_atr"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        score = 45 + 20 * clamp01(-s / 0.15) + 20 * clamp01((adx - p["min_adx"]) / 20) + 15 * clamp01(1 - dist / p["max_dist_ema21_atr"])
        risk = max(p["min_stop_atr"] * atr, e50 - c + p["stop_buffer_atr"] * atr)
        return self._make(fv, "SHORT", score, conds, c + risk, c - p["target_r"] * risk,
                          "Tendência de baixa estabelecida com EMAs alinhadas, inclinação e ADX confirmando.")
