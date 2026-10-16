"""STRATEGY_05 — Pullback Trend: a estratégia original do bot (app/strategy.py), agora como uma estratégia do catálogo.

Reaproveita integralmente strategy.analyze (EMAs 9/21/50, ADX, pullback, candle de confirmação, score 0-145) e
compute_levels (stop técnico). A versão 2.0 corresponde à configuração validada em 2026-10-02 (ADX mínimo 40).
O filtro de 4h/BTC da versão antiga é substituído pelo motor de regime + regimes aceitos.
"""

from ....strategy import StrategyParams, analyze, compute_levels
from ..base import Strategy


class PullbackTrend(Strategy):
    strategy_id = "PULLBACK_TREND"
    name = "Pullback Trend"
    version = "2.0"
    family = "structure"
    default_regimes = ("BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_VOLATILITY_EXPANSION", "BTC_TRANSITION")
    default_min_score = 60
    max_hold_bars = 48
    exit_style = "trend"
    description = ("Estratégia original do bot: pullback até EMA9/EMA21 em tendência forte (EMAs alinhadas, ADX ≥ mínimo), "
                   "com candle de confirmação. Score original 0-145 convertido para 0-100. Stop técnico (fundo/topo do "
                   "pullback ou 1,5 ATR); alvo único no TP2 original (1,67R ou suporte/resistência).")
    feature_keys = ("close", "ema9", "ema21", "ema50", "adx", "rsi", "atr")
    long_params = {"min_adx": 40.0, "min_ema50_slope_pct": 0.02, "max_stop_atr": 3.0}
    short_params = {"min_adx": 40.0, "min_ema50_slope_pct": 0.02, "max_stop_atr": 3.0}

    def generate_signal(self, fv, ctx, directions=("LONG", "SHORT")):
        # pré-filtro barato: o analyze legado reprovaria de qualquer forma (ADX bem abaixo do mínimo)
        if not fv.ok("adx") or fv["adx"] < min(self.long_params["min_adx"], self.short_params["min_adx"]) - 2:
            return []
        window = fv.window(200)
        if len(window) < 100:
            return []
        res = analyze(window, StrategyParams(min_adx=self.long_params["min_adx"],
                                             min_ema50_slope_pct=self.long_params["min_ema50_slope_pct"],
                                             require_confirmation_candle=True, htf_filter_enabled=False,
                                             btc_filter_enabled=False, structural_stop=True,
                                             max_stop_atr=self.long_params["max_stop_atr"]))
        if not res.get("passed") or res["direction"] not in directions:
            return []
        d = res["direction"]
        p = self.long_params if d == "LONG" else self.short_params
        if res["indicators"]["adx"] < p["min_adx"]:
            return []
        snap = res["indicators"]
        price = fv["close"]
        lv = compute_levels(d, price, snap["atr"], snap["support"], snap["resistance"], res["pullback_extreme"],
                            StrategyParams(structural_stop=True, max_stop_atr=p["max_stop_atr"]))
        if lv is None:
            return []
        bd = res["breakdown"]
        conds = [(f"EMAs alinhadas + inclinação ({bd['ema_alignment_slope']}/35)", True),
                 (f"pullback válido até as EMAs ({bd['pullback']}/20)", res["pullback_valid"]),
                 ("candle de confirmação a favor", res["confirmed"]),
                 (f"ADX {snap['adx']:.1f} ≥ {p['min_adx']:.0f}", snap["adx"] >= p["min_adx"]),
                 (f"RSI contextual ({bd['rsi']}/25), volume ({bd['volume']}/20), MACD ({bd['macd']}/10), momentum ({bd['momentum']}/15)", True)]
        score = res["score"] / 145 * 100
        sig = self._make(fv, d, score, conds, lv["stop_loss"], lv["tp2"],
                         f"Pullback {'de alta' if d == 'LONG' else 'de baixa'} em tendência forte, confirmado (score original {res['score']:.0f}/145).",
                         max_stop_atr=p["max_stop_atr"] + 0.1)
        if sig:
            sig.features_used.update({"score_original_145": res["score"], "breakdown": bd})
        return [sig] if sig else []
