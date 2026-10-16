"""Ciclo completo do motor com mercado e corretora simulados (sem rede).

Exercita o caminho real: regime → sinais → filtros → confluência → score → ranking → notícias → DemoExecutionAdapter
(ordens, stop, alvo, diário, snapshot imutável, posição DEMO) → motor de saídas.
"""

import json
from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import select

from app import settings
from app.database import session_scope
from app.models import utcnow
from app.quant import data, engine, registry
from app.quant.models import (DemoPosition, EngineCycle, MarketRegime, Opportunity, QuantConfig, StrategySettings, StrategySignal,
                              TradeFeatures, TradeJournal)
from app.quant.strategies.base import SignalCandidate
from conftest import make_candles, random_walk

N = 300
END = 1_791_000_000_000 - (1_791_000_000_000 % (4 * 3_600_000))


def candles_ending(seed, step=3_600_000, n=N):
    closes, vol = random_walk(n=n, seed=seed)
    return make_candles(closes, vol, start=END - (n - 1) * step, step=step)


class FakeMarket:
    base_url = data.LIVE_URL if hasattr(data, "LIVE_URL") else "https://open-api.bingx.com"
    read_only = True

    def __init__(self):
        self.series = {s: candles_ending(i + 3) for i, s in enumerate(["BTC-USDT", "AAA-USDT", "BBB-USDT", "CCC-USDT"])}

    def get_tickers(self):
        return [{"symbol": s, "lastPrice": c[-1]["close"], "quoteVolume": 5e7} for s, c in self.series.items()]

    def get_book_top(self, symbol):
        p = self.series[symbol][-1]["close"]
        return p * 0.99995, p * 1.00005

    def get_funding_rate(self, symbol):
        return 0.0001

    def get_price(self, symbol):
        return self.series[symbol][-1]["close"]


class FakeExchange:
    base_url = "https://open-api-vst.bingx.com"

    def __init__(self):
        self.orders, self.positions, self.n = {}, [], 0

    def get_contracts(self):
        return {s: {} for s in ("BTC-USDT", "AAA-USDT", "BBB-USDT", "CCC-USDT")}

    def get_positions(self, symbol=None):
        return [p for p in self.positions if symbol is None or p["symbol"] == symbol]

    def get_balance(self):
        return {"equity": 1000.0, "available": 1000.0, "balance": 1000.0}

    def is_hedge_mode(self):
        return False

    def set_margin_type(self, *a, **k):
        pass

    def set_leverage(self, *a, **k):
        pass

    def round_qty(self, s, q):
        return round(q, 3)

    def round_price(self, s, p):
        return round(p, 6)

    def min_qty(self, s, p):
        return 0.001

    def place_order(self, **o):
        self.n += 1
        oid = str(self.n)
        price = FakeMarket().get_price(o["symbol"]) if o["type"] == "MARKET" else o.get("stopPrice")
        self.orders[oid] = {**o, "orderId": oid, "status": "FILLED" if o["type"] == "MARKET" else "NEW",
                            "avgPrice": price, "executedQty": o["quantity"]}
        if o["type"] == "MARKET" and "reduceOnly" not in o:
            self.positions.append({"symbol": o["symbol"], "direction": "LONG" if o["side"] == "BUY" else "SHORT",
                                   "quantity": o["quantity"], "entry_price": price, "mark_price": price,
                                   "unrealized_pnl": 0.0})
        return {"orderId": oid}

    def get_order(self, symbol, oid):
        return self.orders[oid]

    def get_open_orders(self, symbol=None):
        return [o for o in self.orders.values() if o["status"] == "NEW" and (symbol is None or o["symbol"] == symbol)]

    def cancel_order(self, symbol, oid):
        self.orders[oid]["status"] = "CANCELED"


def fixed_signal(sid, symbol, fv_close, score):
    return SignalCandidate(sid, "1.0", "LONG", score, score / 100, f"sinal de teste {sid}", [("condição de teste", True)],
                           fv_close, fv_close * 0.98, fv_close * 1.05, "1h", {"close": fv_close})


@pytest.fixture
def world(monkeypatch):
    mk, ex = FakeMarket(), FakeExchange()
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "LIVE_TRADING", False)
    monkeypatch.setattr(settings, "BINGX_DEMO", True)
    monkeypatch.setattr(data, "market", lambda: mk)
    monkeypatch.setattr(data, "execution", lambda: ex)

    def closed_klines(symbol, interval, limit=300):
        if interval == "4h":
            return candles_ending(99, step=4 * 3_600_000, n=200)
        return mk.series[symbol]
    monkeypatch.setattr(data, "closed_klines", closed_klines)
    import app.news
    monkeypatch.setattr(app.news, "check_news_before_trade",
                        lambda s, d, g=True: {"allow": True, "impact": "neutral", "summary": "teste", "news_count": 0})

    # duas estratégias concordam em AAA-USDT LONG; nada nos outros pares
    for sid, score in (("TREND_EMA", 82.0), ("BREAKOUT", 76.0)):
        strat = registry.BY_ID[sid]
        monkeypatch.setattr(strat, "generate_signal",
                            lambda fv, ctx, directions=("LONG", "SHORT"), sid=sid, score=score:
                            [fixed_signal(sid, "AAA-USDT", fv["close"], score)]
                            if abs(fv["close"] - mk.series["AAA-USDT"][-1]["close"]) < 1e-12 else [])
    registry.sync_definitions()
    with session_scope() as db:
        for st in db.scalars(select(StrategySettings)):
            st.accepted_regimes = ["BTC_BULL_TREND", "BTC_BEAR_TREND", "BTC_RANGE", "BTC_VOLATILITY_EXPANSION",
                                   "BTC_VOLATILITY_CONTRACTION", "BTC_TRANSITION"]
            st.enabled = True
            st.min_score = 0
        db.get(StrategySettings, "BTC_REGIME_FILTER").enabled = False
        qc = db.get(QuantConfig, 1)
        qc.engine_enabled, qc.min_final_score, qc.min_confluence_level = True, 0, "CONFLUENCE"
    return mk, ex


def test_full_cycle_executes_and_journals(world, monkeypatch):
    mk, ex = world
    monkeypatch.setattr("app.executor.FILL_INTERVAL", 0.01)
    res = engine.run_engine(force=True, timeframes=["1h"])["1h"]
    assert res["engine_ready"], res
    assert res["executed"] == ["AAA-USDT"], res

    with session_scope() as db:
        opp = db.scalar(select(Opportunity).where(Opportunity.symbol == "AAA-USDT").order_by(Opportunity.id.desc()))
        assert opp.status == "EXECUTED" and opp.confluence_level in ("CONFLUENCE", "STRONG_CONFLUENCE")
        involved = {opp.primary_strategy, *opp.supporting_strategies}
        assert {"TREND_EMA", "BREAKOUT"} <= involved
        t = db.get(TradeJournal, opp.trade_id)
        assert t.status == "OPEN" and t.execution_mode == "DEMO"
        assert {t.strategy_primary, *t.strategies_supporting} == involved
        assert t.expected_entry_price == mk.get_price("AAA-USDT")
        assert t.stop_reference < t.entry_price < t.target_reference
        assert t.sl_order_id and t.tp_order_id and t.explanation["summary"]
        json.dumps(t.explanation)
        f = db.scalar(select(TradeFeatures).where(TradeFeatures.trade_id == t.id))
        assert f.features["candle_time"] and len(f.signals) == len(involved)
        assert db.scalar(select(DemoPosition).where(DemoPosition.trade_id == t.id)).open
        sigs = db.scalars(select(StrategySignal).where(StrategySignal.opportunity_id == opp.id)).all()
        assert {s.status for s in sigs} == {"USED"}
        cyc = db.scalar(select(EngineCycle).where(EngineCycle.timeframe == "1h", EngineCycle.candle_time == res["candle_time"]))
        assert cyc.summary["executed"] == ["AAA-USDT"] and t.timeframe == "1h"
        tid = t.id
    kinds = sorted(o["type"] for o in ex.orders.values())
    assert kinds == ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]

    # mesmo candle de novo: não duplica sinais nem abre outro trade (já há posição no par)
    res2 = engine.run_engine(force=True, timeframes=["1h"])["1h"]
    assert res2["executed"] == []

    # saída por tempo: o trade "envelhece" e o motor manda fechar na DEMO
    with session_scope() as db:
        # relativo ao relógio sintético dos candles (não ao relógio real, que avança e tornaria o teste instável)
        from datetime import datetime, timezone
        db.get(TradeJournal, tid).entry_time = datetime.fromtimestamp(res["candle_time"] / 1000, timezone.utc) - timedelta(days=5)
    res3 = engine.run_engine(force=True, timeframes=["1h"])["1h"]
    assert any(x["trade_id"] == tid and x["reason"] == "TIME_EXIT" and x["ok"] for x in res3["exits"]), res3["exits"]
    with session_scope() as db:
        assert db.get(TradeJournal, tid).exit_reason == "TIME_EXIT"
    assert any(o["type"] == "MARKET" and o.get("reduceOnly") for o in ex.orders.values())
    _ = np  # noqa


def test_skips_already_processed_candle(world):
    engine.run_engine(force=True, timeframes=["1h"])
    assert "skipped" in engine.run_engine(timeframes=["1h"])["1h"]


def test_cost_filter_blocks_tight_stop(world, monkeypatch):
    """Stop menor que N× (taxas + spread) é descartado com motivo explícito."""
    mk, ex = world
    for sid in ("TREND_EMA", "BREAKOUT"):
        strat = registry.BY_ID[sid]
        monkeypatch.setattr(strat, "generate_signal", lambda fv, ctx, directions=("LONG", "SHORT"), sid=sid:
                            [SignalCandidate(sid, "1.0", "LONG", 80, 0.8, "stop curto", [("c", True)], fv["close"],
                                             fv["close"] * 0.999, fv["close"] * 1.01, "1h", {})]
                            if abs(fv["close"] - mk.series["CCC-USDT"][-1]["close"]) < 1e-12 else [])
    res = engine.run_engine(force=True, timeframes=["1h"])["1h"]
    assert "CCC-USDT" not in res["executed"]
    with session_scope() as db:
        o = db.scalar(select(Opportunity).where(Opportunity.symbol == "CCC-USDT").order_by(Opportunity.id.desc()))
        assert o.status == "SKIPPED" and "custo alto" in o.decision_reasons[0]
