"""STRATEGY_10 — BTC Regime Filter: FILTRO DE CONTEXTO (não gera sinais próprios).

Bloqueia direções incompatíveis com o regime do BTC. As regras são configuráveis e devem ser validadas pelos
resultados (matriz de regime), não assumidas como verdadeiras.
"""

from ..base import Strategy


class BTCRegimeFilter(Strategy):
    strategy_id = "BTC_REGIME_FILTER"
    name = "BTC Regime Filter"
    version = "1.0"
    family = "regime"
    role = "filter"
    default_regimes = ("BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_RANGE", "BTC_VOLATILITY_EXPANSION",
                       "BTC_VOLATILITY_CONTRACTION", "BTC_TRANSITION")
    default_min_score = 0
    exit_style = "none"
    description = ("Filtro de contexto: com o BTC em tendência de baixa bloqueia LONG em altcoins; com o BTC em "
                   "tendência de alta bloqueia SHORT em altcoins. Nos demais regimes, só informa o contexto. "
                   "Não conta como estratégia na confluência.")
    long_params = {"block_long_in_bear": True}
    short_params = {"block_short_in_bull": True}

    def generate_signal(self, fv, ctx, directions=("LONG", "SHORT")):
        return []

    def allows(self, direction: str, btc_regime: str, symbol: str) -> tuple[bool, str]:
        if symbol == "BTC-USDT":
            return True, "BTC não é filtrado por ele mesmo"
        if direction == "LONG" and btc_regime == "BTC_BEAR_TREND" and self.long_params["block_long_in_bear"]:
            return False, "BTC em tendência de baixa: LONG em altcoin bloqueado"
        if direction == "SHORT" and btc_regime == "BTC_BULL_TREND" and self.short_params["block_short_in_bull"]:
            return False, "BTC em tendência de alta: SHORT em altcoin bloqueado"
        return True, f"contexto do BTC compatível com {direction}"
