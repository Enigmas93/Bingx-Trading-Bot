"""STRATEGY_03 — Breakout: rompimento da máxima/mínima dos 20 candles anteriores com volume."""

from ..base import Strategy, clamp01


class Breakout(Strategy):
    strategy_id = "BREAKOUT"
    name = "Breakout"
    version = "1.0"
    family = "trend_following"
    default_regimes = ("BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_VOLATILITY_EXPANSION", "BTC_TRANSITION")
    default_min_score = 65
    max_hold_bars = 36
    exit_style = "trend"
    description = ("Fechamento acima da máxima dos 20 candles anteriores (LONG) ou abaixo da mínima (SHORT), com volume "
                   "acima da média, corpo forte e volatilidade não esgotada. Stop em ATR; alvo em múltiplos do risco.")
    feature_keys = ("close", "open", "don_high20", "don_low20", "vol_ratio", "body_ratio", "atr", "atr_pct_pr120")
    long_params = {"min_vol_ratio": 1.3, "min_body": 0.5, "max_atr_pctile": 0.92, "max_extension_atr": 1.2,
                   "stop_atr": 1.5, "target_r": 2.5}
    short_params = {"min_vol_ratio": 1.4, "min_body": 0.5, "max_atr_pctile": 0.92, "max_extension_atr": 1.2,
                    "stop_atr": 1.5, "target_r": 2.0}

    def _eval(self, fv, direction, p):
        c, o, hi, lo, vr, body, atr, apr = (fv[k] for k in ("close", "open", "don_high20", "don_low20", "vol_ratio",
                                                              "body_ratio", "atr", "atr_pct_pr120"))
        if direction == "LONG":
            broke, ext, candle = c > hi, (c - hi) / atr, c > o
            level_txt = f"fechamento acima da máxima de 20 candles ({hi:.6g})"
        else:
            broke, ext, candle = c < lo, (lo - c) / atr, c < o
            level_txt = f"fechamento abaixo da mínima de 20 candles ({lo:.6g})"
        conds = [
            (level_txt, broke),
            ("candle a favor do rompimento", candle),
            (f"volume ≥ {p['min_vol_ratio']}× a média ({vr:.2f}×)", vr >= p["min_vol_ratio"]),
            (f"corpo ≥ {p['min_body']:.0%} do range ({body:.0%})", body >= p["min_body"]),
            (f"volatilidade não esgotada (percentil ATR {apr:.0%} ≤ {p['max_atr_pctile']:.0%})", not (apr > p["max_atr_pctile"])),
            (f"rompimento não esticado (≤ {p['max_extension_atr']} ATR além do nível)", ext <= p["max_extension_atr"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        score = 45 + 25 * clamp01((vr - p["min_vol_ratio"]) / 1.5) + 15 * clamp01((body - p["min_body"]) / 0.4) + 15 * clamp01(1 - ext / p["max_extension_atr"])
        risk = p["stop_atr"] * atr
        sign = 1 if direction == "LONG" else -1
        return self._make(fv, direction, score, conds, c - sign * risk, c + sign * p["target_r"] * risk,
                          f"Rompimento {'de alta' if sign > 0 else 'de baixa'} do range de 20 candles com volume.")

    def evaluate_long(self, fv, ctx):
        return self._eval(fv, "LONG", self.long_params)

    def evaluate_short(self, fv, ctx):
        return self._eval(fv, "SHORT", self.short_params)
