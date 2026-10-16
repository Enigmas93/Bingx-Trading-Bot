"""STRATEGY_04 — Volatility Expansion: compressão recente seguida de expansão com candle direcional."""

from ..base import Strategy, clamp01


class VolatilityExpansion(Strategy):
    strategy_id = "VOLATILITY_EXPANSION"
    name = "Volatility Expansion"
    version = "1.0"
    family = "volatility"
    default_regimes = ("BTC_VOLATILITY_EXPANSION", "BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_TRANSITION")
    default_min_score = 65
    max_hold_bars = 24
    exit_style = "trend"
    description = ("Procura o início de uma expansão: a largura da Bollinger esteve comprimida (percentil baixo) nos "
                   "últimos 10 candles e agora o ATR acelera, com fechamento fora da banda e corpo forte na direção.")
    feature_keys = ("close", "open", "bb_up", "bb_low", "bb_width_pr_min10", "atr_pct", "atr_pct_ma20", "body_ratio", "atr")
    long_params = {"max_squeeze_pctile": 0.25, "min_atr_expansion": 1.25, "min_body": 0.55, "stop_atr": 1.5, "target_r": 2.5}
    short_params = {"max_squeeze_pctile": 0.25, "min_atr_expansion": 1.3, "min_body": 0.55, "stop_atr": 1.5, "target_r": 2.0}

    def _eval(self, fv, direction, p):
        c, o, up, low, sq, ap, apm, body, atr = (fv[k] for k in self.feature_keys)
        exp = ap / apm if apm and apm > 0 else 0.0
        if direction == "LONG":
            outside, candle, out_txt = c > up, c > o, "fechamento acima da banda superior"
        else:
            outside, candle, out_txt = c < low, c < o, "fechamento abaixo da banda inferior"
        conds = [
            (f"compressão recente (percentil mín. da largura BB em 10 candles {sq:.0%} ≤ {p['max_squeeze_pctile']:.0%})", sq <= p["max_squeeze_pctile"]),
            (f"ATR% acelerou ≥ {p['min_atr_expansion']}× a média ({exp:.2f}×)", exp >= p["min_atr_expansion"]),
            (out_txt, outside),
            ("candle a favor", candle),
            (f"corpo ≥ {p['min_body']:.0%} do range ({body:.0%})", body >= p["min_body"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        score = 45 + 25 * clamp01(1 - sq / p["max_squeeze_pctile"]) + 20 * clamp01((exp - p["min_atr_expansion"]) / 1.0) + 10 * clamp01((body - p["min_body"]) / 0.4)
        risk = p["stop_atr"] * atr
        sign = 1 if direction == "LONG" else -1
        return self._make(fv, direction, score, conds, c - sign * risk, c + sign * p["target_r"] * risk,
                          f"Volatilidade saiu de compressão e expandiu {'para cima' if sign > 0 else 'para baixo'}.")

    def evaluate_long(self, fv, ctx):
        return self._eval(fv, "LONG", self.long_params)

    def evaluate_short(self, fv, ctx):
        return self._eval(fv, "SHORT", self.short_params)
