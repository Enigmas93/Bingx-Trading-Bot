"""Score de oportunidade 0-100 com componentes explícitos e pesos configuráveis (QuantConfig.weights).

Componentes (cada um 0-100, calculados com dados da conta real no candle fechado):
  signal      score da estratégia primária
  regime      compatibilidade da direção com o regime do BTC
  trend       alinhamento de EMAs/inclinação a favor da direção
  volatility  volatilidade em faixa operável (nem morta, nem extrema)
  volume      volume relativo à média dos 20 candles anteriores
  structure   topos/fundos confirmados a favor da direção
  momentum    RSI/MACD a favor sem exaustão
  confluence  nível de confluência e força das estratégias de apoio
  historical  expectativa histórica (backtest) da estratégia primária na direção; 50 = sem dados
base_score = signal; regime_score = regime; confluence_score = confluence; final_score = média ponderada.
"""

import math

from .confluence import ConfluenceGroup
from .models import DEFAULT_WEIGHTS


def _c(x: float) -> float:
    return 0.0 if x is None or math.isnan(x) else max(0.0, min(100.0, x))


def regime_component(direction: str, btc_regime: str, symbol: str) -> float:
    if symbol == "BTC-USDT":
        return 60.0
    table = {
        "BTC_BULL_TREND": (100, 20), "BTC_BEAR_TREND": (20, 100), "BTC_VOLATILITY_EXPANSION": (70, 70),
        "BTC_RANGE": (55, 55), "BTC_VOLATILITY_CONTRACTION": (50, 50), "BTC_TRANSITION": (45, 45),
    }
    lo, sh = table.get(btc_regime, (50, 50))
    return float(lo if direction == "LONG" else sh)


def trend_component(fv, direction: str) -> float:
    c, e21, e50, e100, sl = (fv[k] for k in ("close", "ema21", "ema50", "ema100", "slope50"))
    s = 1 if direction == "LONG" else -1
    checks = [s * (c - e21) > 0, s * (e21 - e50) > 0, s * (e50 - e100) > 0, s * sl > 0]
    return 100.0 * sum(checks) / len(checks)


def volatility_component(fv) -> float:
    pr = fv["atr_pct_pr120"]
    if math.isnan(pr):
        return 50.0
    if 0.25 <= pr <= 0.85:
        return 100.0
    return _c(100 - abs(pr - 0.55) * 180)


def volume_component(fv) -> float:
    vr = fv["vol_ratio"]
    return _c(vr / 2.0 * 100) if not math.isnan(vr) else 50.0


def structure_component(fv, direction: str) -> float:
    sh, psh, sl, psl = (fv[k] for k in ("last_sh", "prev_sh", "last_sl", "prev_sl"))
    if any(math.isnan(x) for x in (sh, psh, sl, psl)):
        return 50.0
    if direction == "LONG":
        pts = (sh > psh) + (sl > psl)
    else:
        pts = (sh < psh) + (sl < psl)
    return 100.0 * pts / 2


def momentum_component(fv, direction: str) -> float:
    rsi, hist, r3 = fv["rsi"], fv["macd_hist"], fv["ret3"]
    s = 1 if direction == "LONG" else -1
    score = 0.0
    score += 35 if s * hist > 0 else 0
    score += 25 if s * r3 > 0 else 0
    if direction == "LONG":
        score += 40 if 50 <= rsi <= 70 else (20 if 40 <= rsi < 50 or 70 < rsi <= 78 else 0)
    else:
        score += 40 if 30 <= rsi <= 50 else (20 if 22 <= rsi < 30 or 50 < rsi <= 60 else 0)
    return _c(score)


def range_component(fv) -> float:
    """Reversão: quanto MENOS tendência no par, melhor (ADX 10 → 100; ADX 25 → 0)."""
    adx = fv["adx"]
    return 50.0 if math.isnan(adx) else _c((25 - adx) / 15 * 100)


def exhaustion_component(fv, direction: str) -> float:
    """Reversão: RSI esticado CONTRA a direção da entrada (LONG com RSI baixo, SHORT com RSI alto)."""
    rsi = fv["rsi"]
    if math.isnan(rsi):
        return 50.0
    x = 50 - rsi if direction == "LONG" else rsi - 50
    return _c((x - 5) / 20 * 100)   # 5 pts do meio → 0; 25 pts (RSI 25/75) → 100


def confluence_component(group: ConfluenceGroup) -> float:
    base = {"SINGLE_SIGNAL": 40, "CONFLUENCE": 75, "STRONG_CONFLUENCE": 100}[group.level]
    sup = group.supporting
    bonus = (sum(s.score for s in sup) / len(sup) - 60) / 4 if sup else 0
    return _c(base + bonus)


def historical_component(expectancy_r: float | None, trades: int) -> float:
    """Expectativa em R mapeada para 0-100 (-0,3R → 0; 0 → 50; +0,3R → 100), encolhida para 50 com poucos trades."""
    if expectancy_r is None or trades <= 0:
        return 50.0
    raw = _c(50 + expectancy_r / 0.3 * 50)
    w = min(1.0, trades / 50)
    return 50 + (raw - 50) * w


def score_group(group: ConfluenceGroup, fv, btc_regime: str, weights: dict | None, hist: dict | None) -> dict:
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    d = group.direction
    hist = hist or {}
    # Estratégias de reversão entram CONTRA o movimento: medir "tendência a favor" e "momentum a favor" as
    # puniria sempre. Para elas, os mesmos pesos medem ausência de tendência e exaustão (RSI esticado).
    reversion = getattr(group.primary, "exit_style", "trend") == "reversion"
    comps = {
        "signal": _c(group.primary.score),
        "regime": regime_component(d, btc_regime, group.symbol),
        "trend": range_component(fv) if reversion else trend_component(fv, d),
        "volatility": volatility_component(fv),
        "volume": volume_component(fv),
        "structure": 50.0 if reversion else structure_component(fv, d),
        "momentum": exhaustion_component(fv, d) if reversion else momentum_component(fv, d),
        "confluence": confluence_component(group),
        "historical": historical_component(hist.get("expectancy"), hist.get("trades", 0)),
    }
    total_w = sum(max(0.0, w.get(k, 0)) for k in comps) or 1.0
    final = sum(comps[k] * max(0.0, w.get(k, 0)) for k in comps) / total_w
    return {
        "base_score": round(comps["signal"], 1), "regime_score": round(comps["regime"], 1),
        "confluence_score": round(comps["confluence"], 1), "final_score": round(final, 1),
        "components": {k: round(v, 1) for k, v in comps.items()},
        "weights": {k: w.get(k, 0) for k in comps},
        "style": "reversion" if reversion else "trend",
    }
