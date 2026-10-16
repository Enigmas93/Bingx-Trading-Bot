"""Motor de regime do BTC.

Classificações (exclusivas, nesta prioridade):
  BTC_VOLATILITY_EXPANSION    ATR% 1h atual > 1,2 × média do ATR% de 48h a 24h atrás
  BTC_BULL_TREND              tendência 4h BULL e ADX 1h >= 20
  BTC_BEAR_TREND              tendência 4h BEAR e ADX 1h >= 20
  BTC_VOLATILITY_CONTRACTION  largura da Bollinger 1h no percentil < 20% das 120h anteriores e ATR% no percentil < 30%
  BTC_RANGE                   ADX 1h < 20 e tendência 4h NEUTRAL
  BTC_TRANSITION              qualquer outro caso (sinais mistos)
Estado de mercado: BULLISH (4h BULL), BEARISH (4h BEAR) ou NEUTRAL.

Usa só candles FECHADOS. As regras são hipóteses configuráveis por estratégia (regimes aceitos) e são avaliadas
pelos resultados (matriz estratégia × regime), não assumidas como verdade.
"""

import math

import numpy as np

from ..strategy import trend_of
from . import features as F

REGIMES = ["BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_RANGE", "BTC_VOLATILITY_EXPANSION",
           "BTC_VOLATILITY_CONTRACTION", "BTC_TRANSITION"]
REGIME_LABELS = {
    "BTC_BULL_TREND": "BTC em tendência de alta",
    "BTC_BEAR_TREND": "BTC em tendência de baixa",
    "BTC_RANGE": "BTC lateral",
    "BTC_VOLATILITY_EXPANSION": "BTC com volatilidade em expansão",
    "BTC_VOLATILITY_CONTRACTION": "BTC com volatilidade comprimida",
    "BTC_TRANSITION": "BTC em transição (sinais mistos)",
}

EXPANSION_MULT = 1.2
TREND_ADX = 20.0
CONTRACTION_BBW = 0.20
CONTRACTION_ATR = 0.30


def classify_at(f1h: dict, i: int, trend4h: str) -> dict:
    """Classifica o regime no candle 1h i (fechado) dado a tendência 4h conhecida nesse instante."""
    atrp = float(f1h["atr_pct"][i])
    ref = f1h["atr_pct"][i - 48:i - 24] if i >= 48 else np.array([])
    ref_mean = float(np.nanmean(ref)) if len(ref) and not np.all(np.isnan(ref)) else math.nan
    adx = float(f1h["adx"][i])
    bbw_pr = float(f1h["bb_width_pr120"][i])
    atr_pr = float(f1h["atr_pct_pr120"][i])
    c = f1h["close"]
    ret24 = float(c[i] / c[i - 24] - 1) * 100 if i >= 24 else math.nan
    expansion = not math.isnan(ref_mean) and atrp > EXPANSION_MULT * ref_mean
    contraction = (not math.isnan(bbw_pr) and bbw_pr < CONTRACTION_BBW and not math.isnan(atr_pr) and atr_pr < CONTRACTION_ATR)
    if expansion:
        regime = "BTC_VOLATILITY_EXPANSION"
    elif trend4h == "BULL" and adx >= TREND_ADX:
        regime = "BTC_BULL_TREND"
    elif trend4h == "BEAR" and adx >= TREND_ADX:
        regime = "BTC_BEAR_TREND"
    elif contraction:
        regime = "BTC_VOLATILITY_CONTRACTION"
    elif adx < TREND_ADX and trend4h == "NEUTRAL":
        regime = "BTC_RANGE"
    else:
        regime = "BTC_TRANSITION"
    state = {"BULL": "BULLISH", "BEAR": "BEARISH"}.get(trend4h, "NEUTRAL")
    return {
        "btc_regime": regime,
        "market_state": state,
        "label": REGIME_LABELS[regime],
        "candle_time": int(f1h["time"][i]),
        "btc_close": round(float(c[i]), 2),
        "trend_4h": trend4h,
        "adx_1h": _r(adx),
        "atr_pct_1h": _r(atrp),
        "atr_pct_ref_24_48h": _r(ref_mean),
        "expansion_ratio": _r(atrp / ref_mean) if ref_mean and not math.isnan(ref_mean) else None,
        "bb_width_pctile_120h": _r(bbw_pr),
        "atr_pct_pctile_120h": _r(atr_pr),
        "btc_ret_24h_pct": _r(ret24),
        "rules": {"expansion_mult": EXPANSION_MULT, "trend_adx": TREND_ADX, "contraction_bbw_pctile": CONTRACTION_BBW,
                  "contraction_atr_pctile": CONTRACTION_ATR},
    }


def _r(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 5)


class RegimeSeries:
    """Regime do BTC em cada fechamento de candle 1h (para o backtest), sem olhar o futuro."""

    def __init__(self, btc1h: list[dict], btc4h: list[dict]):
        self.f1h = F.compute(btc1h)
        self.close_1h = [k["time"] + 3_600_000 for k in btc1h]
        close_4h = [k["time"] + 14_400_000 for k in btc4h]
        trends4h = []
        for j in range(len(btc4h)):
            trends4h.append(trend_of(btc4h[max(0, j - 119):j + 1]) if j >= 60 else "NEUTRAL")
        self._t4 = (close_4h, trends4h)
        self.regimes = []
        import bisect
        for i in range(len(btc1h)):
            t_close = self.close_1h[i]
            j = bisect.bisect_right(close_4h, t_close) - 1
            tr = trends4h[j] if j >= 0 else "NEUTRAL"
            self.regimes.append(classify_at(self.f1h, i, tr)["btc_regime"] if i >= 60 else "BTC_TRANSITION")
        self._bisect = bisect

    def at(self, t_ms: int) -> str:
        """Regime conhecido no instante t_ms (último candle 1h do BTC fechado até t_ms)."""
        i = self._bisect.bisect_right(self.close_1h, t_ms) - 1
        return self.regimes[i] if i >= 0 else "BTC_TRANSITION"


def classify_live(btc1h_closed: list[dict], btc4h_closed: list[dict]) -> dict:
    f1h = F.compute(btc1h_closed)
    trend4h = trend_of(btc4h_closed[-120:]) if len(btc4h_closed) >= 60 else "NEUTRAL"
    return classify_at(f1h, len(btc1h_closed) - 1, trend4h)
