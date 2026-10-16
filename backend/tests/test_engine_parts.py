"""Regime, confluência, score, ranking, saídas, deduplicação, imutabilidade, atribuição, versionamento."""

from collections import Counter
from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import session_scope
from app.models import utcnow
from app.quant import features as F
from app.quant import performance, registry
from app.quant.confluence import ConfluenceGroup, build_groups, level_for
from app.quant.exits import evaluate_exit
from app.quant.features import FeatureView
from app.quant.models import (ImmutableSnapshotError, QuantConfig, StrategyDefinition, StrategySignal,
                              TradeFeatures, TradeJournal)
from app.quant.ranking import rank_and_decide
from app.quant.regime import RegimeSeries, classify_at
from app.quant.scoring import historical_component, score_group
from app.quant.strategies.base import SignalCandidate
from conftest import make_candles, random_walk


def sig(sid, d="LONG", score=70, price=100.0, rr=2.0):
    stop = price - 2 if d == "LONG" else price + 2
    tgt = price + 2 * rr if d == "LONG" else price - 2 * rr
    return SignalCandidate(sid, "1.0", d, score, score / 100, "r", [("c", True)], price, stop, tgt, "1h")


# ---------------------------------------------------------------- regime
def _btc(kind, n=600):
    rng = np.random.default_rng(3)
    if kind == "bull":
        r = rng.normal(0.003, 0.004, n)
    elif kind == "flat":  # oscila em torno de 100 (sem tendência)
        return make_candles(100 * (1 + 0.004 * np.sin(np.arange(n) / 2.0) + rng.normal(0, 0.0005, n)), wick=0.0005)
    else:
        r = rng.normal(0, 0.002, n)
        r[-6:] = rng.normal(0, 0.03, 6)
    return make_candles(100 * np.exp(np.cumsum(r)), wick=0.0005)


def test_regime_classification():
    bull = _btc("bull")
    assert classify_at(F.compute(bull), len(bull) - 1, "BULL")["btc_regime"] == "BTC_BULL_TREND"
    flat = _btc("flat")
    assert classify_at(F.compute(flat), len(flat) - 1, "NEUTRAL")["btc_regime"] in ("BTC_RANGE", "BTC_VOLATILITY_CONTRACTION")
    spike = _btc("spike")
    assert classify_at(F.compute(spike), len(spike) - 1, "NEUTRAL")["btc_regime"] == "BTC_VOLATILITY_EXPANSION"


def test_regime_series_no_lookahead():
    closes, _ = random_walk(n=1200, seed=11)
    h1 = make_candles(closes)
    h4 = make_candles(closes[::4], step=4 * 3_600_000)
    full = RegimeSeries(h1, h4)
    part = RegimeSeries(h1[:900], [k for k in h4 if k["time"] + 4 * 3_600_000 <= h1[899]["time"] + 3_600_000])
    for i in range(300, 900, 7):
        t = h1[i]["time"] + 3_600_000
        assert full.at(t) == part.at(t), i


# ---------------------------------------------------------------- confluência e score
def test_confluence_levels_and_primary():
    assert [level_for(n) for n in (1, 2, 3, 5)] == ["SINGLE_SIGNAL", "CONFLUENCE", "STRONG_CONFLUENCE", "STRONG_CONFLUENCE"]
    groups = build_groups("ETH-USDT", 1, [sig("A", score=70), sig("B", score=80), sig("C", "SHORT", 90)])
    by = {g.direction: g for g in groups}
    assert by["LONG"].level == "CONFLUENCE" and by["LONG"].primary.strategy_id == "B"
    assert [s.strategy_id for s in by["LONG"].supporting] == ["A"]
    assert by["SHORT"].level == "SINGLE_SIGNAL"
    assert by["LONG"].group_id == "CG-1-ETH-USDT-LONG"


@pytest.fixture(scope="module")
def fv():
    closes, vol = random_walk(n=600, seed=5)
    c = make_candles(closes, vol)
    return FeatureView(F.compute(c), len(c) - 1, c)


def test_score_range_and_weights(fv):
    g1 = ConfluenceGroup("ETH-USDT", "LONG", 1, [sig("A", score=70)])
    g3 = ConfluenceGroup("ETH-USDT", "LONG", 1, [sig("A", score=70), sig("B", score=72), sig("C", score=75)])
    s1 = score_group(g1, fv, "BTC_BULL_TREND", None, None)
    s3 = score_group(g3, fv, "BTC_BULL_TREND", None, None)
    for s in (s1, s3):
        assert 0 <= s["final_score"] <= 100
    assert s3["confluence_score"] > s1["confluence_score"] and s3["final_score"] > s1["final_score"]
    only_signal = score_group(g1, fv, "BTC_BULL_TREND", {k: 0 for k in s1["weights"]} | {"signal": 1}, None)
    assert only_signal["final_score"] == 70
    bear = score_group(g1, fv, "BTC_BEAR_TREND", None, None)
    assert bear["regime_score"] < s1["regime_score"]


def test_historical_component_shrinks():
    assert historical_component(None, 0) == 50
    assert historical_component(0.3, 5) < historical_component(0.3, 100) == 100


# ---------------------------------------------------------------- ranking
def ctx(**kw):
    base = dict(min_final_score=60, min_confluence_level="CONFLUENCE", single_signal_min_score=85, conflict_margin=15,
                max_positions=3, max_same_direction=3, max_open_per_strategy=4, max_correlation=0.85,
                open_symbols=set(), open_count=0, open_dirs=Counter(), open_per_strategy=Counter(), risk_paused=None,
                correlation=lambda a, b: 0.0, open_list=[], engine_ready=True, engine_reason="")
    base.update(kw)
    return base


def opp(sym, d="LONG", score=70, lvl="CONFLUENCE", strat="A"):
    return {"symbol": sym, "direction": d, "final_score": score, "confluence_level": lvl, "primary_strategy": strat,
            "reward_r": 2}


def test_ranking_orders_and_explains():
    res = rank_and_decide([opp("A", score=65), opp("B", score=80), opp("C", score=50)], ctx())
    assert [o["symbol"] for o in res] == ["B", "A", "C"]
    assert res[0]["status"] == "SELECTED" and res[2]["status"] == "SKIPPED"
    assert all(o["reasons"] for o in res)


def test_ranking_single_signal_rule():
    res = {o["symbol"]: o for o in rank_and_decide([opp("A", score=80, lvl="SINGLE_SIGNAL"),
                                                    opp("B", score=90, lvl="SINGLE_SIGNAL")], ctx())}
    assert res["A"]["status"] == "SKIPPED" and res["B"]["status"] == "SELECTED"


def test_ranking_conflict_and_limits():
    res = rank_and_decide([opp("X", "LONG", 70), opp("X", "SHORT", 65)], ctx())
    assert all(o["status"] == "CONFLICT" for o in res)
    res = {o["direction"]: o for o in rank_and_decide([opp("X", "LONG", 90), opp("X", "SHORT", 65)], ctx())}
    assert res["LONG"]["status"] == "SELECTED" and res["SHORT"]["status"] == "CONFLICT"
    res = rank_and_decide([opp("A", score=90), opp("B", score=80)], ctx(max_positions=1))
    assert [o["status"] for o in res] == ["SELECTED", "SKIPPED"]
    res = rank_and_decide([opp("A", score=90)], ctx(open_symbols={"A"}))
    assert res[0]["status"] == "SKIPPED"
    res = rank_and_decide([opp("A", score=90), opp("B", score=80)], ctx(correlation=lambda a, b: 0.95))
    assert [o["status"] for o in res] == ["SELECTED", "SKIPPED"]
    res = rank_and_decide([opp("A", score=90)], ctx(engine_ready=False, engine_reason="LIVE"))
    assert res[0]["status"] == "SKIPPED"


# ---------------------------------------------------------------- saídas
class Cfg:
    exit_on_regime_change = True
    exit_on_momentum_loss = True
    exit_on_opposite_signal = True


def trade(**kw):
    t = {"entry_time": utcnow() - timedelta(hours=5), "direction": "LONG", "symbol": "ETH-USDT",
         "strategy_primary": "TREND_EMA", "btc_regime": "BTC_BULL_TREND"}
    t.update(kw)
    return t


def test_exit_rules():
    now = int(utcnow().timestamp() * 1000)
    assert evaluate_exit(trade(), None, "BTC_BULL_TREND", [], Cfg, 4, "trend", now)[0] == "TIME_EXIT"
    assert evaluate_exit(trade(), None, "BTC_BEAR_TREND", [], Cfg, 48, "trend", now)[0] == "REGIME_CHANGE"
    assert evaluate_exit(trade(), None, "BTC_BULL_TREND", ["BREAKOUT"], Cfg, 48, "trend", now)[0] == "EXIT_SIGNAL"
    assert evaluate_exit(trade(), None, "BTC_RANGE", [], Cfg, 48, "trend", now) is None


# ---------------------------------------------------------------- banco: dedupe, imutabilidade, atribuição, versão
def _signal_row(**kw):
    d = dict(candle_time=123, strategy_id="TREND_EMA", strategy_version="1.0", symbol="ETH-USDT", direction="LONG",
             timeframe="1h", score=70, confidence=0.7, reason="r", conditions=[], regime="BTC_RANGE", ref_price=1,
             stop=0.9, target=1.2, features={}, status="ACCEPTED")
    d.update(kw)
    return StrategySignal(**d)


def test_no_duplicate_signal_per_candle():
    with session_scope() as db:
        db.add(_signal_row())
    with pytest.raises(IntegrityError):
        with session_scope() as db:
            db.add(_signal_row())
    with session_scope() as db:  # outra direção no mesmo candle é um sinal diferente
        db.add(_signal_row(direction="SHORT"))


def _journal(**kw):
    d = dict(trade_uid=f"T-{np.random.randint(1e9)}", status="CLOSED", symbol="ETH-USDT", direction="LONG",
             timeframe="1h", expected_entry_price=100, entry_price=100, exit_price=104, quantity=1, risk_usdt=2,
             strategy_primary="TREND_EMA", strategy_primary_version="1.0", strategies_supporting=["BREAKOUT"],
             strategy_versions={"TREND_EMA": "1.0", "BREAKOUT": "1.0"}, strategy_scores={"TREND_EMA": 80, "BREAKOUT": 70},
             confluence_level="CONFLUENCE", confluence_group_id="CG-1", base_score=80, regime_score=100,
             confluence_score=75, final_score=78, market_regime="ALTA", btc_regime="BTC_BULL_TREND",
             market_state="BULLISH", entry_reason="x", stop_reference=98, target_reference=104, pnl=4, pnl_r=2.0,
             entry_time=utcnow(), exit_time=utcnow())
    d.update(kw)
    return TradeJournal(**d)


def test_features_snapshot_immutable():
    with session_scope() as db:
        t = _journal()
        db.add(t)
        db.flush()
        db.add(TradeFeatures(trade_id=t.id, features={"rsi": 55}, signals=[], regime={}, opportunity={}))
        tid = t.id
    with pytest.raises(ImmutableSnapshotError):
        with session_scope() as db:
            f = db.scalar(select(TradeFeatures).where(TradeFeatures.trade_id == tid))
            f.features = {"rsi": 99}
    with session_scope() as db:
        assert db.scalar(select(TradeFeatures).where(TradeFeatures.trade_id == tid)).features == {"rsi": 55}


def test_attribution_primary_vs_participant():
    registry.sync_definitions()
    with session_scope() as db:
        db.add(_journal(pnl_r=-1.0, pnl=-2, strategy_primary="BREAKOUT", strategies_supporting=["TREND_EMA"]))
    performance.refresh_demo()
    p = performance._perf("DEMO", "BREAKOUT", "ALL", "primary")
    q = performance._perf("DEMO", "BREAKOUT", "ALL", "participant")
    assert p.executions == 1 and p.expectancy == -1.0
    assert q.executions >= 2   # também participou do trade em que TREND_EMA foi primária
    with session_scope() as db:
        assert db.scalar(select(QuantConfig).limit(1)).auto_disable is False


def test_metrics():
    m = performance.metrics([{"r": 2, "direction": "LONG"}, {"r": -1, "direction": "SHORT"}, {"r": -1, "direction": "LONG"}])
    assert m["trades"] == 3 and m["win_rate"] == 33.3 and m["profit_factor"] == 1.0 and m["max_drawdown"] == 2
    assert m["long_trades"] == 2 and m["short_trades"] == 1


def test_disable_keeps_history_and_versioning(monkeypatch):
    registry.sync_definitions()
    registry.update_settings("TREND_EMA", {"enabled": False})
    with session_scope() as db:
        assert db.scalars(select(TradeJournal).where(TradeJournal.strategy_primary == "TREND_EMA")).first() is not None
    assert registry.settings_map()["TREND_EMA"]["health"] == "DISABLED"
    registry.update_settings("TREND_EMA", {"enabled": True})
    with pytest.raises(ValueError):
        registry.update_settings("TREND_EMA", {"long_params": {}})
    with pytest.raises(ValueError):
        registry.update_settings("TREND_EMA", {"accepted_regimes": ["NOPE"]})
    # mudar a definição sem trocar a versão bloqueia a estratégia e preserva a definição gravada
    s = registry.BY_ID["MOMENTUM_ADX_RSI"]
    monkeypatch.setattr(s, "long_params", {**s.long_params, "min_adx": 99.0})
    rep = registry.sync_definitions()
    assert rep["conflicts"] and registry.blocked_reason("MOMENTUM_ADX_RSI")
    with session_scope() as db:
        row = db.scalar(select(StrategyDefinition).where(StrategyDefinition.strategy_id == "MOMENTUM_ADX_RSI"))
        assert row.definition["long_params"]["min_adx"] != 99.0
    monkeypatch.undo()
    registry.sync_definitions()
    assert registry.blocked_reason("MOMENTUM_ADX_RSI") is None


def test_reversion_scored_on_its_own_terms(fv):
    """Uma reversão não pode ser punida por estar contra a tendência (bug: score final travava em ~50)."""
    trend = sig("TREND_EMA", score=80)
    rev = sig("RANGE_REVERSION", score=80)
    rev.exit_style = "reversion"
    st = score_group(ConfluenceGroup("ETH-USDT", "LONG", 1, [trend]), fv, "BTC_RANGE", None, None)
    sr = score_group(ConfluenceGroup("ETH-USDT", "LONG", 1, [rev]), fv, "BTC_RANGE", None, None)
    assert sr["style"] == "reversion" and st["style"] == "trend"
    assert sr["components"]["structure"] == 50.0
    from app.quant.scoring import exhaustion_component, range_component
    class FV(dict):
        def __getitem__(self, k): return dict.get(self, k)
    assert exhaustion_component(FV(rsi=25.0), "LONG") == 100 and exhaustion_component(FV(rsi=25.0), "SHORT") == 0
    assert range_component(FV(adx=10.0)) == 100 and range_component(FV(adx=30.0)) == 0


def test_finalize_keeps_engine_exit_reason_against_stale_snapshot(monkeypatch):
    """Corrida: o motor grava o motivo e fecha; o monitor tinha um retrato antigo (sem motivo). Não pode virar
    EXTERNAL_CLOSE."""
    from app.quant import monitor
    with session_scope() as db:
        t = _journal(status="OPEN", exit_reason="REGIME_CHANGE", exit_detail="BTC virou", sl_order_id="1",
                     tp_order_id="2", pnl=None, pnl_r=None, exit_time=None)
        db.add(t)
        db.flush()
        snap = {c.name: getattr(t, c.name) for c in TradeJournal.__table__.columns}
    snap["exit_reason"] = None            # retrato tirado antes de o motor decidir fechar

    class C:
        def get_order(self, s, oid): return {"status": "CANCELLED"}
        def get_income(self, *a, **k): return []
        def cancel_order(self, *a): pass

    class A:
        client = C()
        def cancel_protection(self, *a): pass

    monkeypatch.setattr(monitor, "_excursions", lambda t, c: (None, None))
    monkeypatch.setattr(monitor.data, "market", lambda: type("M", (), {"get_price": lambda self, s: 101.0})())
    assert monitor._finalize(A(), snap)
    with session_scope() as db:
        assert db.get(TradeJournal, snap["id"]).exit_reason == "REGIME_CHANGE"


def test_blocked_hours_window():
    from datetime import datetime, timezone
    from app.quant.engine import blocked_hours

    class Q:
        block_hours_enabled, block_start_hour, block_end_hour, block_timezone = True, 23, 6, "America/Sao_Paulo"
    utc = lambda h, m=0: datetime(2026, 10, 5, h, m, tzinfo=timezone.utc)  # noqa: E731  (Brasília = UTC-3)
    assert blocked_hours(Q, utc(2, 0))            # 23:00 local
    assert blocked_hours(Q, utc(8, 59))           # 05:59 local
    assert blocked_hours(Q, utc(9, 0)) is None    # 06:00 local libera
    assert blocked_hours(Q, utc(1, 59)) is None   # 22:59 local
    Q.block_hours_enabled = False
    assert blocked_hours(Q, utc(4)) is None


def test_risk_reset_ignores_older_losses():
    from datetime import timedelta
    from app.quant.engine import _risk_pause

    class Cfg:
        daily_loss_limit_pct, max_consecutive_losses = 0, 2
    with session_scope() as db:
        for _ in range(3):
            db.add(_journal(pnl=-1.0, pnl_r=-0.5, exit_time=utcnow() + timedelta(seconds=1)))
    assert "perdas seguidas" in (_risk_pause(Cfg, None) or "")
    assert _risk_pause(Cfg, None, utcnow() + timedelta(seconds=2)) is None


def test_closed_klines_uses_clock(monkeypatch):
    """Na virada do candle a API pode não ter o candle novo: o último candle já fechado não pode ser descartado."""
    import time
    from app.quant import data
    now = int(time.time() * 1000)
    step = 300_000
    last_open = (now // step) * step - step          # último candle 5m JÁ fechado
    k = [{"time": last_open - i * step} for i in range(3, -1, -1)]

    class M:
        _time_offset_ms = 0
        def get_klines(self, *a, **kw): return list(k)
    monkeypatch.setattr(data, "market", lambda: M())
    assert data.closed_klines("X", "5m", 10)[-1]["time"] == last_open      # API sem o candle novo
    k.append({"time": last_open + step})                                    # API já criou o candle em formação
    assert data.closed_klines("X", "5m", 10)[-1]["time"] == last_open


def test_momentum_exit_only_on_configured_timeframes():
    now = int(utcnow().timestamp() * 1000)

    class FV(dict):
        def __getitem__(self, k): return dict.get(self, k)
    fv = FV(close=99.0, ema21=100.0, macd_hist=-0.5)       # LONG perdeu momentum

    class Cfg2(Cfg):
        momentum_exit_timeframes = ["1h"]
    t1 = trade(timeframe="1h")
    t15 = trade(timeframe="15m")
    assert evaluate_exit(t1, fv, "BTC_RANGE", [], Cfg2, 48, "trend", now)[0] == "MOMENTUM_LOSS"
    assert evaluate_exit(t15, fv, "BTC_RANGE", [], Cfg2, 480, "trend", now, 900_000) is None
