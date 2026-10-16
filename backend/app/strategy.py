"""Lógica pura da estratégia, compartilhada pelo scanner (ao vivo) e pelo backtest."""

import math
from dataclasses import dataclass

import numpy as np

from . import indicators as ind

MAX_SCORE = 145
# Alvos em múltiplos do risco (R). Com stop = 1.5 ATR, equivalem a TP1 1.5 / TP2 2.5 / TP3 4 ATR.
TP1_R, TP2_R, TP3_R = 1.0, 5 / 3, 8 / 3


@dataclass
class StrategyParams:
    min_adx: float = 0.0
    min_ema50_slope_pct: float = 0.02
    require_confirmation_candle: bool = True
    htf_filter_enabled: bool = True
    btc_filter_enabled: bool = True
    structural_stop: bool = True
    max_stop_atr: float = 3.0
    min_stop_cost_ratio: float = 4.0
    fee_rate_pct: float = 0.05

    @classmethod
    def from_config(cls, cfg) -> "StrategyParams":
        return cls(**{f: getattr(cfg, f) for f in cls.__dataclass_fields__})


def min_score_for_adx(adx: float) -> float | None:
    """Sistema de bloqueio em 3 níveis. None = bloqueio total."""
    if adx < 18:
        return None  # mercado lateral
    if adx < 22:
        return 85  # tendência fraca
    if adx <= 30:
        return 75  # tendência moderada
    return 65  # tendência forte


def trend_of(candles: list[dict]) -> str:
    """Tendência de um timeframe (usado no HTF e no BTC): BULL | BEAR | NEUTRAL."""
    if len(candles) < 60:
        return "NEUTRAL"
    c = np.array([k["close"] for k in candles], dtype=float)
    e21, e50 = ind.ema(c, 21), ind.ema(c, 50)
    slope = ind.slope_pct(e50, 5)
    if math.isnan(slope):
        return "NEUTRAL"
    if c[-1] > e50[-1] and e21[-1] > e50[-1] and slope > 0:
        return "BULL"
    if c[-1] < e50[-1] and e21[-1] < e50[-1] and slope < 0:
        return "BEAR"
    return "NEUTRAL"


def regime_filter(direction: str, htf_trend: str | None, btc_trend: str | None,
                  is_btc: bool, params: StrategyParams) -> str | None:
    """Retorna o motivo da rejeição, ou None se o regime permite a operação."""
    if params.htf_filter_enabled:
        wanted = "BULL" if direction == "LONG" else "BEAR"
        if htf_trend != wanted:
            return f"Timeframe maior não confirma {direction} (tendência {htf_trend or 'indisponível'})"
    if params.btc_filter_enabled and not is_btc and btc_trend is not None:
        if direction == "LONG" and btc_trend == "BEAR":
            return "BTC em tendência de baixa: LONG em alt bloqueado"
        if direction == "SHORT" and btc_trend == "BULL":
            return "BTC em tendência de alta: SHORT em alt bloqueado"
    return None


def analyze(candles: list[dict], params: StrategyParams) -> dict:
    """Analisa candles FECHADOS (ordem cronológica). Retorna direção, score e motivo."""
    if len(candles) < 100:
        return {"passed": False, "reason": f"Histórico insuficiente ({len(candles)} candles)"}

    o = np.array([k["open"] for k in candles], dtype=float)
    h = np.array([k["high"] for k in candles], dtype=float)
    l = np.array([k["low"] for k in candles], dtype=float)
    c = np.array([k["close"] for k in candles], dtype=float)
    v = np.array([k["volume"] for k in candles], dtype=float)

    ema9, ema21, ema50 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)
    rsi = ind.rsi(c, 14)
    atr = ind.atr(h, l, c, 14)
    adx, _, _ = ind.adx(h, l, c, 14)
    macd_line, macd_signal, macd_hist = ind.macd(c)
    bb_upper, bb_mid, bb_lower = ind.bollinger(c, 20, 2)
    slope = ind.slope_pct(ema50, 5)

    close = c[-1]
    snap = {
        "close": close, "ema9": ema9[-1], "ema21": ema21[-1], "ema50": ema50[-1],
        "ema50_slope_pct": slope, "rsi": rsi[-1], "atr": atr[-1], "adx": adx[-1],
        "macd": macd_line[-1], "macd_signal": macd_signal[-1], "macd_hist": macd_hist[-1],
        "bb_upper": bb_upper[-1], "bb_mid": bb_mid[-1], "bb_lower": bb_lower[-1],
    }
    if any(math.isnan(x) for x in snap.values()):
        return {"passed": False, "reason": "Indicadores incompletos (NaN)"}

    support, resistance = ind.swing_levels(h, l, close)
    snap.update({"support": support, "resistance": resistance})
    snap = {k: (round(float(x), 8) if x is not None else None) for k, x in snap.items()}
    e9, e21, e50, a = snap["ema9"], snap["ema21"], snap["ema50"], snap["atr"]

    # ---------------- Bloqueio por ADX (CRÍTICO)
    min_score = min_score_for_adx(snap["adx"])
    if min_score is None:
        return {"passed": False, "reason": f"ADX {snap['adx']:.1f} < 18: mercado lateral (bloqueio total)",
                "indicators": snap}
    if snap["adx"] < params.min_adx:
        return {"passed": False, "reason": f"ADX {snap['adx']:.1f} < mínimo configurado {params.min_adx:.0f}",
                "indicators": snap}

    # ---------------- Validação de tendência (+ inclinação mínima da EMA50)
    ema_bull = e9 > e21 > e50
    ema_bear = e9 < e21 < e50
    if ema_bull and slope > 0:
        direction = "LONG"
    elif ema_bear and slope < 0:
        direction = "SHORT"
    elif ema_bull or ema_bear:
        trend = "BULLISH" if ema_bull else "BEARISH"
        return {"passed": False, "reason": f"Contradição: EMAs {trend} mas slope EMA50 = {slope:.3f}%",
                "indicators": snap}
    else:
        return {"passed": False, "reason": "EMAs sem alinhamento (sem tendência definida)", "indicators": snap}
    if abs(slope) < params.min_ema50_slope_pct:
        return {"passed": False, "direction": direction, "indicators": snap,
                "reason": f"Slope EMA50 fraco ({slope:.3f}% < {params.min_ema50_slope_pct}%)"}
    is_long = direction == "LONG"
    sign = 1 if is_long else -1

    breakdown: dict[str, float] = {}

    # 1. EMA Alignment + Slope (35): 20 pelo alinhamento + até 15 pela força do slope
    slope_ref = 3 * params.min_ema50_slope_pct
    slope_strength = min(1.0, abs(slope) / slope_ref) if slope_ref > 0 else 1.0
    breakdown["ema_alignment_slope"] = round(20 + 15 * slope_strength)

    # 2. Pullback válido, próximo das EMAs (20), graduado pela profundidade
    if is_long:
        extreme = float(l[-3:].min())
        touched21 = extreme <= e21 + 0.3 * a
        touched9 = extreme <= e9 + 0.25 * a
        holding = close > e50 and close >= e21 - 0.5 * a
        overextended = close - e9 > 1.0 * a
    else:
        extreme = float(h[-3:].max())
        touched21 = extreme >= e21 - 0.3 * a
        touched9 = extreme >= e9 - 0.25 * a
        holding = close < e50 and close <= e21 + 0.5 * a
        overextended = e9 - close > 1.0 * a
    near = abs(close - e21) <= 1.0 * a
    pullback_valid = bool((touched9 or touched21 or near) and holding and not overextended)
    breakdown["pullback"] = (20 if touched21 else 14 if touched9 else 8) if pullback_valid else 0
    pullback_extreme = float(l[-5:].min()) if is_long else float(h[-5:].max())

    # 3. RSI contextual (25)
    r = snap["rsi"]
    if 40 <= r <= 60:
        rsi_pts = 25
    elif is_long:
        rsi_pts = 15 if (35 <= r < 40 or 60 < r <= 65) else 5 if 65 < r <= 70 else 0
    else:
        rsi_pts = 15 if (60 < r <= 65 or 35 <= r < 40) else 5 if 30 <= r < 35 else 0
    breakdown["rsi"] = rsi_pts

    # 4. Volume (20), graduado
    avg_vol = v[-21:-1].mean()
    vol_ratio = float(v[-1] / avg_vol) if avg_vol > 0 else 0.0
    breakdown["volume"] = 20 if vol_ratio >= 1.3 else 14 if vol_ratio >= 1.0 else 8 if vol_ratio >= 0.8 else 0
    snap["volume_ratio"] = round(vol_ratio, 3)

    # 5. Volatilidade controlada (10)
    atr_pct = a / close * 100
    breakdown["volatility"] = 10 if 0.3 <= atr_pct <= 3.0 else 5 if 0.2 <= atr_pct <= 5.0 else 0
    snap["atr_pct"] = round(atr_pct, 4)

    # 6. Bollinger (10): num pullback, o ideal é o preço perto da banda do meio (não esticado)
    width = snap["bb_upper"] - snap["bb_lower"]
    pct_b = (close - snap["bb_lower"]) / width if width > 0 else 0.5
    pos = pct_b if is_long else 1 - pct_b  # 0 = extremo contra a tendência, 1 = esticado a favor
    breakdown["bollinger"] = 10 if 0.3 <= pos <= 0.65 else 5 if (0.15 <= pos < 0.3 or 0.65 < pos <= 0.85) else 0
    snap["bb_pct_b"] = round(float(pct_b), 4)

    # 7. MACD (10): histograma virando a favor (5) + linha vs sinal (3) + linha do lado certo do zero (2)
    hist_turning = sign * (macd_hist[-1] - macd_hist[-2]) > 0
    breakdown["macd"] = ((5 if hist_turning else 0) + (3 if sign * (snap["macd"] - snap["macd_signal"]) > 0 else 0)
                         + (2 if sign * snap["macd"] > 0 else 0))

    # 8. Momentum (15)
    momentum = 0
    momentum += 5 if sign * (close - o[-1]) > 0 else 0  # candle a favor
    momentum += 5 if sign * (close - c[-2]) > 0 else 0  # fechou além do anterior
    momentum += 5 if sign * (close - c[-4]) > 0 else 0  # ROC(3) a favor
    breakdown["momentum"] = momentum

    # Candle de confirmação: o pullback precisa ter voltado a favor da tendência
    confirmed = sign * (close - o[-1]) > 0 and sign * (close - e9) > 0 and sign * (close - c[-2]) > 0

    score = float(sum(breakdown.values()))
    result = {
        "direction": direction, "score": score, "min_score": min_score, "pullback_valid": pullback_valid,
        "confirmed": confirmed, "breakdown": breakdown, "indicators": snap, "pullback_extreme": pullback_extreme,
    }
    if not pullback_valid:
        return {**result, "passed": False, "reason": f"Score {score:.0f} mas sem pullback válido"}
    if params.require_confirmation_candle and not confirmed:
        return {**result, "passed": False, "reason": f"Score {score:.0f} sem candle de confirmação"}
    if score < min_score:
        return {**result, "passed": False, "reason": f"Score {score:.0f} < mínimo {min_score:.0f} (ADX {snap['adx']:.1f})"}
    return {**result, "passed": True,
            "reason": f"{direction} score {score:.0f}/{MAX_SCORE} (mín {min_score:.0f}, ADX {snap['adx']:.1f})"}


def compute_levels(direction: str, entry: float, atr: float, support: float | None, resistance: float | None,
                   pullback_extreme: float | None, params: StrategyParams) -> dict | None:
    """Stop = 1.5 ATR (ou além do fundo/topo do pullback, se mais largo). TPs em múltiplos do risco.

    Com stop de 1.5 ATR os alvos ficam exatamente como na especificação: TP1 1.5 / TP2 2.5 / TP3 4 ATR.
    Retorna None se o stop técnico exigir mais que max_stop_atr x ATR.
    """
    risk = 1.5 * atr
    if params.structural_stop and pullback_extreme is not None:
        structural = (entry - (pullback_extreme - 0.2 * atr)) if direction == "LONG" \
            else ((pullback_extreme + 0.2 * atr) - entry)
        risk = max(risk, structural)
    if risk > params.max_stop_atr * atr:
        return None
    if direction == "LONG":
        sl, tp1, tp2, tp3 = entry - risk, entry + TP1_R * risk, entry + TP2_R * risk, entry + TP3_R * risk
        if resistance is not None and tp1 < resistance < tp2:
            tp2 = resistance
    else:
        sl, tp1, tp2, tp3 = entry + risk, entry - TP1_R * risk, entry - TP2_R * risk, entry - TP3_R * risk
        if support is not None and tp2 < support < tp1:
            tp2 = support
    return {"stop_loss": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3}


def cost_filter(entry: float, stop_loss: float, spread_pct: float, params: StrategyParams) -> str | None:
    """Rejeita quando a distância do stop é pequena demais perto do custo (spread + taxas de ida e volta)."""
    stop_pct = abs(entry - stop_loss) / entry * 100
    cost_pct = spread_pct + 2 * params.fee_rate_pct
    if stop_pct < params.min_stop_cost_ratio * cost_pct:
        return (f"Stop de {stop_pct:.3f}% pequeno demais para o custo de {cost_pct:.3f}% "
                f"(mínimo {params.min_stop_cost_ratio}x)")
    return None
