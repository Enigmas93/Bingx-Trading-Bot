"""Estratégias: níveis válidos, LONG/SHORT independentes, espelhamento, features causais."""

import math

import numpy as np
import pytest

from app.quant import features as F
from app.quant.features import FeatureView
from app.quant.registry import SIGNAL_STRATEGIES, STRATEGIES
from conftest import make_candles, random_walk, reflect

CTX = {"btc_regime": "BTC_TRANSITION"}


def _signals(candles, strategies=SIGNAL_STRATEGIES):
    feats = F.compute(candles)
    out = []
    for i in range(210, len(candles)):
        fv = FeatureView(feats, i, candles)
        for s in strategies:
            for sig in s.generate_signal(fv, CTX):
                out.append((i, sig))
    return out


@pytest.fixture(scope="module")
def rw():
    closes, vol = random_walk(n=2500)
    return make_candles(closes, vol)


@pytest.fixture(scope="module")
def rw_signals(rw):
    return _signals(rw)


def test_interface_complete():
    for s in STRATEGIES:
        meta = s.get_metadata()
        for k in ("strategy_id", "name", "version", "family", "timeframe", "supported_directions", "default_regimes"):
            assert meta[k] not in (None, "")
        assert s.validate() == []
        assert callable(s.generate_signal) and callable(s.explain_signal) and callable(s.calculate_features)


def test_catalog_has_variants_per_timeframe():
    from app.quant.registry import BY_ID, TIMEFRAMES
    assert TIMEFRAMES == ["1h", "15m", "5m"]
    assert len(STRATEGIES) == 9 * 3 + 1
    assert [s.strategy_id for s in STRATEGIES if s.role == "filter"] == ["BTC_REGIME_FILTER"]
    for base in ("TREND_EMA", "PULLBACK_TREND"):
        assert BY_ID[base].timeframe == "1h"            # 1h mantém o id original (histórico preservado)
        assert BY_ID[f"{base}_15M"].timeframe == "15m" and BY_ID[f"{base}_5M"].timeframe == "5m"
        assert BY_ID[f"{base}_5M"].base_id == base
    assert BY_ID["TREND_EMA_5M"].default_cooldown_minutes < BY_ID["TREND_EMA"].default_cooldown_minutes
    assert BY_ID["TREND_EMA_5M"].definition()["timeframe"] == "5m"


def test_signals_are_produced_and_valid(rw_signals):
    assert len(rw_signals) > 20
    produced = {s.strategy_id for _, s in rw_signals}
    assert len(produced) >= 5, produced
    for _, s in rw_signals:
        assert 0 <= s.score <= 100 and 0 <= s.confidence <= 1
        assert all(ok for _, ok in s.conditions), (s.strategy_id, s.conditions)
        if s.direction == "LONG":
            assert s.stop < s.ref_price < s.target
        else:
            assert s.target < s.ref_price < s.stop
        assert s.reward_r >= 0.99
        assert s.features_used and s.reason


def test_both_directions_present(rw_signals):
    dirs = {s.direction for _, s in rw_signals}
    assert dirs == {"LONG", "SHORT"}


@pytest.mark.parametrize("sid", ["MARKET_STRUCTURE", "RANGE_REVERSION", "SR_REVERSAL"])
def test_mirror_symmetry(rw, sid):
    """Série espelhada: cada LONG da original vira SHORT (e vice-versa) nas estratégias de regras simétricas."""
    strat = [s for s in SIGNAL_STRATEGIES if s.strategy_id == sid]
    assert strat
    a = {(i, s.direction) for i, s in _signals(rw, strat)}
    b = {(i, "LONG" if s.direction == "SHORT" else "SHORT") for i, s in _signals(reflect(rw), strat)}
    assert a, f"{sid} não gerou sinais na série de teste"
    overlap = len(a & b) / max(len(a | b), 1)
    assert overlap >= 0.9, (sid, len(a), len(b), overlap)


def test_long_short_parameters_independent():
    s = next(x for x in SIGNAL_STRATEGIES if x.strategy_id == "BREAKOUT")
    assert s.long_params is not s.short_params
    before = dict(s.long_params)
    s.short_params["min_vol_ratio"] = s.short_params["min_vol_ratio"]  # sem efeito cruzado
    assert s.long_params == before


def test_disabled_direction_not_generated(rw):
    feats = F.compute(rw)
    for i in range(210, len(rw)):
        fv = FeatureView(feats, i, rw)
        for s in SIGNAL_STRATEGIES:
            for sig in s.generate_signal(fv, CTX, directions=("LONG",)):
                assert sig.direction == "LONG"


def test_features_are_causal(rw):
    """O valor em i não muda quando candles futuros são adicionados (sem look-ahead)."""
    full = F.compute(rw)
    cut = 1500
    part = F.compute(rw[:cut + 1])
    for k in F.SNAPSHOT_KEYS:
        a, b = full[k][cut], part[k][cut]
        if isinstance(a, float) and math.isnan(a):
            assert math.isnan(b), k
        else:
            assert np.isclose(a, b, rtol=1e-9, atol=1e-12), (k, a, b)


def test_explain_signal_readable(rw_signals):
    _, sig = rw_signals[0]
    s = next(x for x in SIGNAL_STRATEGIES if x.strategy_id == sig.strategy_id)
    txt = s.explain_signal(sig)
    assert sig.strategy_id in txt and "stop" in txt and "Motivo" in txt


def test_btc_filter_rules():
    from app.quant.registry import BY_ID
    f = BY_ID["BTC_REGIME_FILTER"]
    assert f.generate_signal(None, CTX) == []
    assert not f.allows("LONG", "BTC_BEAR_TREND", "ETH-USDT")[0]
    assert not f.allows("SHORT", "BTC_BULL_TREND", "ETH-USDT")[0]
    assert f.allows("LONG", "BTC_BULL_TREND", "ETH-USDT")[0]
    assert f.allows("SHORT", "BTC_BULL_TREND", "BTC-USDT")[0]
