"""STRATEGY_08 — Volume Expansion: candle de volume anormal com corpo forte rompendo o candle anterior."""

from ..base import Strategy, clamp01


class VolumeExpansion(Strategy):
    strategy_id = "VOLUME_EXPANSION"
    name = "Volume Expansion"
    version = "1.0"
    family = "microstructure"
    default_regimes = ("BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_VOLATILITY_EXPANSION", "BTC_TRANSITION")
    default_min_score = 65
    max_hold_bars = 24
    exit_style = "trend"
    description = ("Volume pelo menos 2× a média dos 20 candles anteriores, corpo dominante e fechamento além da "
                   "máxima (LONG) ou mínima (SHORT) do candle anterior, do lado certo da EMA21. Stop além do candle.")
    feature_keys = ("close", "open", "high", "low", "vol_ratio", "body_ratio", "ema21", "atr")
    long_params = {"min_vol_ratio": 2.0, "min_body": 0.6, "stop_buffer_atr": 0.2, "target_r": 2.0}
    short_params = {"min_vol_ratio": 2.2, "min_body": 0.6, "stop_buffer_atr": 0.2, "target_r": 2.0}

    def _eval(self, fv, direction, p):
        c, o, hi, lo, vr, body, e21, atr = (fv[k] for k in self.feature_keys)
        ph, pl = fv.prev("high"), fv.prev("low")
        if direction == "LONG":
            conds = [(f"volume ≥ {p['min_vol_ratio']}× a média ({vr:.2f}×)", vr >= p["min_vol_ratio"]),
                     (f"corpo ≥ {p['min_body']:.0%} do range ({body:.0%})", body >= p["min_body"]),
                     ("candle de alta", c > o),
                     (f"fechamento acima da máxima anterior ({ph:.6g})", c > ph),
                     ("acima da EMA21", c > e21)]
            stop = lo - p["stop_buffer_atr"] * atr
        else:
            conds = [(f"volume ≥ {p['min_vol_ratio']}× a média ({vr:.2f}×)", vr >= p["min_vol_ratio"]),
                     (f"corpo ≥ {p['min_body']:.0%} do range ({body:.0%})", body >= p["min_body"]),
                     ("candle de baixa", c < o),
                     (f"fechamento abaixo da mínima anterior ({pl:.6g})", c < pl),
                     ("abaixo da EMA21", c < e21)]
            stop = hi + p["stop_buffer_atr"] * atr
        if not all(ok for _, ok in conds):
            return None
        risk = abs(c - stop)
        if risk < 0.8 * atr:  # candle pequeno: usa ao menos 0,8 ATR de distância
            risk = 0.8 * atr
            stop = c - risk if direction == "LONG" else c + risk
        sign = 1 if direction == "LONG" else -1
        score = 45 + 35 * clamp01((vr - p["min_vol_ratio"]) / 3) + 20 * clamp01((body - p["min_body"]) / 0.4)
        return self._make(fv, direction, score, conds, stop, c + sign * p["target_r"] * risk,
                          f"Volume anormal com candle {'comprador' if sign > 0 else 'vendedor'} dominante.")

    def evaluate_long(self, fv, ctx):
        return self._eval(fv, "LONG", self.long_params)

    def evaluate_short(self, fv, ctx):
        return self._eval(fv, "SHORT", self.short_params)
