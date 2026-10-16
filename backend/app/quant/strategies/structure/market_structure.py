"""STRATEGY_09 — Market Structure: topos/fundos ascendentes (HH/HL) e rompimento do último topo (ou o espelho)."""

import math

from ..base import Strategy, clamp01


class MarketStructure(Strategy):
    strategy_id = "MARKET_STRUCTURE"
    name = "Market Structure"
    version = "1.0"
    family = "structure"
    default_regimes = ("BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_VOLATILITY_EXPANSION", "BTC_TRANSITION")
    default_min_score = 65
    max_hold_bars = 48
    exit_style = "trend"
    description = ("LONG: os dois últimos topos e fundos confirmados são ascendentes (HH + HL) e o candle atual fecha acima "
                   "do último topo pela primeira vez (quebra de estrutura). SHORT: LH + LL e quebra do último fundo. "
                   "Stop além do último fundo/topo; alvo em múltiplos do risco.")
    feature_keys = ("close", "last_sh", "prev_sh", "last_sl", "prev_sl", "atr")
    long_params = {"max_break_atr": 1.0, "stop_buffer_atr": 0.2, "target_r": 2.0}
    short_params = {"max_break_atr": 1.0, "stop_buffer_atr": 0.2, "target_r": 2.0}

    def evaluate_long(self, fv, ctx):
        p = self.long_params
        c, sh, psh, sl, psl, atr = (fv[k] for k in self.feature_keys)
        prev_c = fv.prev("close")
        if any(math.isnan(x) for x in (sh, psh, sl, psl, prev_c)):
            return None
        brk = (c - sh) / atr
        conds = [
            (f"topo mais alto (HH: {sh:.6g} > {psh:.6g})", sh > psh),
            (f"fundo mais alto (HL: {sl:.6g} > {psl:.6g})", sl > psl),
            (f"fechamento acima do último topo {sh:.6g}", c > sh),
            ("quebra nova (candle anterior ainda abaixo do topo)", prev_c <= sh),
            (f"quebra não esticada (≤ {p['max_break_atr']} ATR)", brk <= p["max_break_atr"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        stop = sl - p["stop_buffer_atr"] * atr
        risk = c - stop
        score = 45 + 25 * clamp01((sl - psl) / (2 * atr)) + 15 * clamp01((sh - psh) / (2 * atr)) + 15 * clamp01(1 - brk / p["max_break_atr"])
        return self._make(fv, "LONG", score, conds, stop, c + p["target_r"] * risk,
                          "Estrutura de alta (HH + HL) com quebra do último topo.")

    def evaluate_short(self, fv, ctx):
        p = self.short_params
        c, sh, psh, sl, psl, atr = (fv[k] for k in self.feature_keys)
        prev_c = fv.prev("close")
        if any(math.isnan(x) for x in (sh, psh, sl, psl, prev_c)):
            return None
        brk = (sl - c) / atr
        conds = [
            (f"topo mais baixo (LH: {sh:.6g} < {psh:.6g})", sh < psh),
            (f"fundo mais baixo (LL: {sl:.6g} < {psl:.6g})", sl < psl),
            (f"fechamento abaixo do último fundo {sl:.6g}", c < sl),
            ("quebra nova (candle anterior ainda acima do fundo)", prev_c >= sl),
            (f"quebra não esticada (≤ {p['max_break_atr']} ATR)", brk <= p["max_break_atr"]),
        ]
        if not all(ok for _, ok in conds):
            return None
        stop = sh + p["stop_buffer_atr"] * atr
        risk = stop - c
        score = 45 + 25 * clamp01((psh - sh) / (2 * atr)) + 15 * clamp01((psl - sl) / (2 * atr)) + 15 * clamp01(1 - brk / p["max_break_atr"])
        return self._make(fv, "SHORT", score, conds, stop, c - p["target_r"] * risk,
                          "Estrutura de baixa (LH + LL) com quebra do último fundo.")
