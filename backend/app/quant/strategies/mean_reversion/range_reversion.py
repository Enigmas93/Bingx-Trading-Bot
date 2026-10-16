"""STRATEGY_07 — Range Mean Reversion: em mercado sem tendência, extremos da Bollinger tendem a voltar à média."""

from ..base import Strategy, clamp01


class RangeMeanReversion(Strategy):
    strategy_id = "RANGE_REVERSION"
    name = "Range Mean Reversion"
    version = "1.0"
    family = "mean_reversion"
    default_regimes = ("BTC_RANGE", "BTC_VOLATILITY_CONTRACTION")
    default_min_score = 65
    max_hold_bars = 16
    exit_style = "reversion"
    description = ("Só em ausência de tendência (ADX baixo): fechamento na/abaixo da banda inferior com RSI sobrevendido "
                   "(LONG) ou na/acima da superior com RSI sobrecomprado (SHORT). Alvo na média (banda do meio); stop em ATR.")
    feature_keys = ("close", "adx", "pct_b", "rsi", "bb_mid", "atr")
    long_params = {"max_adx": 20.0, "max_pct_b": 0.05, "max_rsi": 32.0, "stop_atr": 1.2, "min_reward_r": 1.0}
    short_params = {"max_adx": 20.0, "min_pct_b": 0.95, "min_rsi": 68.0, "stop_atr": 1.2, "min_reward_r": 1.0}

    def evaluate_long(self, fv, ctx):
        p = self.long_params
        c, adx, pb, rsi, mid, atr = (fv[k] for k in self.feature_keys)
        conds = [
            (f"sem tendência (ADX {adx:.1f} < {p['max_adx']:.0f})", adx < p["max_adx"]),
            (f"preço na banda inferior (%B {pb:.2f} ≤ {p['max_pct_b']})", pb <= p["max_pct_b"]),
            (f"RSI sobrevendido ({rsi:.1f} ≤ {p['max_rsi']:.0f})", rsi <= p["max_rsi"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        score = 45 + 25 * clamp01((p["max_pct_b"] - pb) / 0.25) + 20 * clamp01((p["max_rsi"] - rsi) / 12) + 10 * clamp01((p["max_adx"] - adx) / 10)
        return self._make(fv, "LONG", score, conds, c - p["stop_atr"] * atr, mid,
                          "Extremo inferior da Bollinger em mercado sem tendência: retorno à média esperado.",
                          min_reward_r=p["min_reward_r"])

    def evaluate_short(self, fv, ctx):
        p = self.short_params
        c, adx, pb, rsi, mid, atr = (fv[k] for k in self.feature_keys)
        conds = [
            (f"sem tendência (ADX {adx:.1f} < {p['max_adx']:.0f})", adx < p["max_adx"]),
            (f"preço na banda superior (%B {pb:.2f} ≥ {p['min_pct_b']})", pb >= p["min_pct_b"]),
            (f"RSI sobrecomprado ({rsi:.1f} ≥ {p['min_rsi']:.0f})", rsi >= p["min_rsi"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        score = 45 + 25 * clamp01((pb - p["min_pct_b"]) / 0.25) + 20 * clamp01((rsi - p["min_rsi"]) / 12) + 10 * clamp01((p["max_adx"] - adx) / 10)
        return self._make(fv, "SHORT", score, conds, c + p["stop_atr"] * atr, mid,
                          "Extremo superior da Bollinger em mercado sem tendência: retorno à média esperado.",
                          min_reward_r=p["min_reward_r"])
