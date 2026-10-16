"""STRATEGY_02 — Momentum ADX + RSI: força direcional crescente com RSI na zona de momentum."""

from ..base import Strategy, clamp01


class MomentumADXRSI(Strategy):
    strategy_id = "MOMENTUM_ADX_RSI"
    name = "Momentum ADX + RSI"
    version = "1.0"
    family = "momentum"
    default_regimes = ("BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_VOLATILITY_EXPANSION")
    default_min_score = 65
    max_hold_bars = 24
    exit_style = "trend"
    description = ("ADX acima do mínimo e subindo, +DI > −DI (LONG), RSI na zona de momentum (não sobrecomprado), "
                   "histograma do MACD a favor e preço acima de 3 candles atrás. Stop em ATR; alvo em múltiplos do risco.")
    feature_keys = ("close", "adx", "adx_slope3", "pdi", "mdi", "rsi", "macd_hist", "ret3", "atr")
    long_params = {"min_adx": 25.0, "rsi_low": 55.0, "rsi_high": 72.0, "stop_atr": 1.5, "target_r": 2.0}
    short_params = {"min_adx": 25.0, "rsi_low": 28.0, "rsi_high": 45.0, "stop_atr": 1.5, "target_r": 2.0}

    def evaluate_long(self, fv, ctx):
        p = self.long_params
        adx, sl, pdi, mdi, rsi, mh, r3, atr = (fv[k] for k in ("adx", "adx_slope3", "pdi", "mdi", "rsi", "macd_hist", "ret3", "atr"))
        c = fv["close"]
        conds = [
            (f"ADX ≥ {p['min_adx']:.0f} ({adx:.1f})", adx >= p["min_adx"]),
            (f"ADX subindo em 3 candles ({sl:+.2f})", sl > 0),
            ("+DI > −DI", pdi > mdi),
            (f"RSI entre {p['rsi_low']:.0f} e {p['rsi_high']:.0f} ({rsi:.1f})", p["rsi_low"] <= rsi <= p["rsi_high"]),
            ("histograma do MACD positivo", mh > 0),
            ("preço acima de 3 candles atrás", r3 > 0),
        ]
        if not all(ok for _, ok in conds):
            return None
        mid = (p["rsi_low"] + p["rsi_high"]) / 2
        score = 45 + 20 * clamp01((adx - p["min_adx"]) / 20) + 15 * clamp01(sl / 5) + 20 * clamp01(1 - abs(rsi - mid) / ((p["rsi_high"] - p["rsi_low"]) / 2))
        risk = p["stop_atr"] * atr
        return self._make(fv, "LONG", score, conds, c - risk, c + p["target_r"] * risk,
                          "Momentum de alta: força direcional crescente com RSI e MACD confirmando.")

    def evaluate_short(self, fv, ctx):
        p = self.short_params
        adx, sl, pdi, mdi, rsi, mh, r3, atr = (fv[k] for k in ("adx", "adx_slope3", "pdi", "mdi", "rsi", "macd_hist", "ret3", "atr"))
        c = fv["close"]
        conds = [
            (f"ADX ≥ {p['min_adx']:.0f} ({adx:.1f})", adx >= p["min_adx"]),
            (f"ADX subindo em 3 candles ({sl:+.2f})", sl > 0),
            ("−DI > +DI", mdi > pdi),
            (f"RSI entre {p['rsi_low']:.0f} e {p['rsi_high']:.0f} ({rsi:.1f})", p["rsi_low"] <= rsi <= p["rsi_high"]),
            ("histograma do MACD negativo", mh < 0),
            ("preço abaixo de 3 candles atrás", r3 < 0),
        ]
        if not all(ok for _, ok in conds):
            return None
        mid = (p["rsi_low"] + p["rsi_high"]) / 2
        score = 45 + 20 * clamp01((adx - p["min_adx"]) / 20) + 15 * clamp01(sl / 5) + 20 * clamp01(1 - abs(rsi - mid) / ((p["rsi_high"] - p["rsi_low"]) / 2))
        risk = p["stop_atr"] * atr
        return self._make(fv, "SHORT", score, conds, c + risk, c - p["target_r"] * risk,
                          "Momentum de baixa: força direcional crescente com RSI e MACD confirmando.")
