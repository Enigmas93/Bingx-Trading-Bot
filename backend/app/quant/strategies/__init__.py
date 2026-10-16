"""Catálogo de estratégias (uma hipótese por arquivo, organizadas por família)."""

from .mean_reversion.range_reversion import RangeMeanReversion
from .microstructure.volume_expansion import VolumeExpansion
from .momentum.adx_momentum import MomentumADXRSI
from .regime.btc_regime import BTCRegimeFilter
from .structure.market_structure import MarketStructure
from .structure.pullback import PullbackTrend
from .structure.support_resistance import SupportResistanceReversal
from .trend_following.breakout import Breakout
from .trend_following.ema_trend import TrendEMA
from .volatility.volatility_expansion import VolatilityExpansion

CATALOG = [TrendEMA, MomentumADXRSI, Breakout, VolatilityExpansion, PullbackTrend, SupportResistanceReversal,
           RangeMeanReversion, VolumeExpansion, MarketStructure, BTCRegimeFilter]
