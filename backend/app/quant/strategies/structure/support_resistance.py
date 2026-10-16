"""STRATEGY_06 — Support/Resistance Reversal: rejeição de suporte (LONG) ou resistência (SHORT) confirmada."""

import math

from ..base import Strategy, clamp01


class SupportResistanceReversal(Strategy):
    strategy_id = "SR_REVERSAL"
    name = "Support/Resistance Reversal"
    version = "1.0"
    family = "structure"
    default_regimes = ("BTC_RANGE", "BTC_VOLATILITY_CONTRACTION", "BTC_TRANSITION")
    default_min_score = 65
    max_hold_bars = 24
    exit_style = "reversion"
    description = ("Preço testa o último fundo confirmado (suporte) e é rejeitado: pavio inferior grande, fechamento "
                   "acima do suporte e RSI baixo (LONG). Espelho em resistência para SHORT. Stop além do nível; alvo na "
                   "EMA50 ou no topo/fundo oposto, exigindo ao menos 1,2R.")
    feature_keys = ("close", "open", "low", "high", "last_sl", "last_sh", "last_sl_age", "last_sh_age", "lower_wick",
                    "upper_wick", "rsi", "ema50", "atr")
    long_params = {"touch_tol_atr": 0.3, "min_wick": 0.45, "max_rsi": 45.0, "max_level_age": 60, "stop_buffer_atr": 0.4, "min_reward_r": 1.2}
    short_params = {"touch_tol_atr": 0.3, "min_wick": 0.45, "min_rsi": 55.0, "max_level_age": 60, "stop_buffer_atr": 0.4, "min_reward_r": 1.2}

    def evaluate_long(self, fv, ctx):
        p = self.long_params
        c, o, lo, sup, res, age, wick, rsi, e50, atr = (fv[k] for k in ("close", "open", "low", "last_sl", "last_sh",
                                                                         "last_sl_age", "lower_wick", "rsi", "ema50", "atr"))
        if math.isnan(sup):
            return None
        conds = [
            (f"mínima tocou o suporte {sup:.6g} (tolerância {p['touch_tol_atr']} ATR)", lo <= sup + p["touch_tol_atr"] * atr),
            ("fechamento acima do suporte", c > sup),
            (f"suporte recente (≤ {p['max_level_age']} candles; {age:.0f})", age <= p["max_level_age"]),
            (f"pavio inferior ≥ {p['min_wick']:.0%} do range ({wick:.0%})", wick >= p["min_wick"]),
            ("candle de alta", c > o),
            (f"RSI ≤ {p['max_rsi']:.0f} ({rsi:.1f})", rsi <= p["max_rsi"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        stop = min(lo, sup) - p["stop_buffer_atr"] * atr
        risk = c - stop
        target = max(x for x in (e50, res, c + p["min_reward_r"] * risk) if not math.isnan(x))
        score = 45 + 25 * clamp01((wick - p["min_wick"]) / 0.4) + 20 * clamp01((p["max_rsi"] - rsi) / 20) + 10 * clamp01(1 - abs(lo - sup) / (p["touch_tol_atr"] * atr + 1e-12))
        return self._make(fv, "LONG", score, conds, stop, target,
                          "Rejeição clara do suporte com pavio longo e RSI baixo.", min_reward_r=p["min_reward_r"])

    def evaluate_short(self, fv, ctx):
        p = self.short_params
        c, o, hi, res, sup, age, wick, rsi, e50, atr = (fv[k] for k in ("close", "open", "high", "last_sh", "last_sl",
                                                                         "last_sh_age", "upper_wick", "rsi", "ema50", "atr"))
        if math.isnan(res):
            return None
        conds = [
            (f"máxima tocou a resistência {res:.6g} (tolerância {p['touch_tol_atr']} ATR)", hi >= res - p["touch_tol_atr"] * atr),
            ("fechamento abaixo da resistência", c < res),
            (f"resistência recente (≤ {p['max_level_age']} candles; {age:.0f})", age <= p["max_level_age"]),
            (f"pavio superior ≥ {p['min_wick']:.0%} do range ({wick:.0%})", wick >= p["min_wick"]),
            ("candle de baixa", c < o),
            (f"RSI ≥ {p['min_rsi']:.0f} ({rsi:.1f})", rsi >= p["min_rsi"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        stop = max(hi, res) + p["stop_buffer_atr"] * atr
        risk = stop - c
        target = min(x for x in (e50, sup, c - p["min_reward_r"] * risk) if not math.isnan(x))
        score = 45 + 25 * clamp01((wick - p["min_wick"]) / 0.4) + 20 * clamp01((rsi - p["min_rsi"]) / 20) + 10 * clamp01(1 - abs(hi - res) / (p["touch_tol_atr"] * atr + 1e-12))
        return self._make(fv, "SHORT", score, conds, stop, target,
                          "Rejeição clara da resistência com pavio longo e RSI alto.", min_reward_r=p["min_reward_r"])
