"""Monitor × corretora: falhas de leitura não podem fechar trades nem cancelar proteções (incidentes 09/10–16/10)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import settings
from app.database import session_scope
from app.models import utcnow
from app.quant import data, monitor
from app.quant.models import TradeJournal


class FakeEx:
    base_url = "https://open-api-vst.bingx.com"

    def __init__(self):
        self.positions = []
        self.blank_reads = 0          # quantas leituras seguidas da lista geral voltam vazias (falha da API)
        self.orders = {}
        self.income = []
        self.n = 0
        self.cancelled = []

    def get_positions(self, symbol=None):
        if symbol is None and self.blank_reads > 0:
            self.blank_reads -= 1
            return []
        return [p for p in self.positions if symbol is None or p["symbol"] == symbol]

    def get_open_orders(self, symbol=None):
        return [o for o in self.orders.values() if o["status"] == "NEW" and (symbol is None or o["symbol"] == symbol)]

    def get_order(self, symbol, oid):
        return self.orders.get(oid, {"status": "CANCELLED"})

    def get_income(self, *a, **k):
        return list(self.income)

    def cancel_order(self, symbol, oid):
        self.cancelled.append(oid)
        if oid in self.orders:
            self.orders[oid]["status"] = "CANCELLED"

    def place_order(self, **o):
        self.n += 1
        oid = f"o{self.n}"
        self.orders[oid] = {**o, "orderId": oid, "status": "FILLED" if o["type"] == "MARKET" else "NEW",
                            "avgPrice": 100.0}
        if o["type"] == "MARKET" and o.get("reduceOnly"):
            self.positions = [p for p in self.positions if p["symbol"] != o["symbol"]]
        return {"orderId": oid}

    def is_hedge_mode(self):
        return False

    def round_price(self, s, p):
        return round(p, 6)

    def get_balance(self):
        return {"equity": 1000.0, "available": 1000.0}


class FakeMk:
    def __init__(self, px=100.0):
        self.px = px

    def get_price(self, s):
        return self.px

    def get_klines(self, *a, **k):
        return []


@pytest.fixture
def env(monkeypatch):
    ex, mk = FakeEx(), FakeMk()
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "LIVE_TRADING", False)
    monkeypatch.setattr(settings, "BINGX_DEMO", True)
    monkeypatch.setattr(data, "execution", lambda: ex)
    monkeypatch.setattr(data, "market", lambda: mk)
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    monkeypatch.setattr("app.quant.execution.time.sleep", lambda s: None)
    monkeypatch.setattr("app.quant.execution.data.execution", lambda: ex)
    with session_scope() as db:   # isola: nenhum outro trade OPEN de testes anteriores
        for t in db.scalars(select(TradeJournal).where(TradeJournal.status == "OPEN")):
            t.status, t.exit_time, t.pnl_confirmed = "CLOSED", utcnow() - timedelta(days=30), True
    monitor._misses.clear()
    monitor._alerted.clear()
    return ex, mk


def _open_trade(ex, sym="ZZZ-USDT", qty=10.0):
    ex.orders["sl1"] = {"symbol": sym, "type": "STOP_MARKET", "status": "NEW", "orderId": "sl1"}
    ex.orders["tp1"] = {"symbol": sym, "type": "TAKE_PROFIT_MARKET", "status": "NEW", "orderId": "tp1"}
    ex.positions = [{"symbol": sym, "direction": "LONG", "quantity": qty, "entry_price": 100.0, "mark_price": 100.0,
                     "unrealized_pnl": 0.0}]
    with session_scope() as db:
        t = TradeJournal(trade_uid=f"T-mon-{sym}-{utcnow().timestamp()}", status="OPEN", symbol=sym, direction="LONG",
                         timeframe="1h", entry_time=utcnow() - timedelta(hours=1), expected_entry_price=100,
                         entry_price=100, quantity=qty, risk_usdt=20, strategy_primary="TREND_EMA",
                         strategy_primary_version="1.0", confluence_level="CONFLUENCE", confluence_group_id="g",
                         base_score=80, regime_score=50, confluence_score=75, final_score=78, market_regime="x",
                         btc_regime="BTC_RANGE", market_state="NEUTRAL", entry_reason="t", stop_reference=98,
                         target_reference=104, current_stop=98, sl_order_id="sl1", tp_order_id="tp1")
        db.add(t)
        db.flush()
        return t.id


def test_blank_position_list_does_not_close_trade(env):
    ex, _ = env
    tid = _open_trade(ex)
    for _ in range(5):              # a lista geral volta vazia várias vezes seguidas
        ex.blank_reads = 2
        monitor.run_monitor()
    with session_scope() as db:
        assert db.get(TradeJournal, tid).status == "OPEN"
    assert "sl1" not in ex.cancelled and "tp1" not in ex.cancelled


def test_real_close_needs_confirmation_then_finalizes(env):
    ex, _ = env
    tid = _open_trade(ex)
    ex.positions = []
    ex.orders["sl1"]["status"] = "FILLED"
    ex.orders["sl1"]["avgPrice"] = 98.0
    ex.income = [{"incomeType": "REALIZED_PNL", "income": "-20"}, {"incomeType": "TRADING_FEE", "income": "-1"}]
    monitor.run_monitor()           # 1ª ausência: ainda não finaliza
    with session_scope() as db:
        assert db.get(TradeJournal, tid).status == "OPEN"
    monitor.run_monitor()           # 2ª ausência + evidência: finaliza com o motivo correto
    with session_scope() as db:
        t = db.get(TradeJournal, tid)
        assert t.status == "CLOSED" and t.exit_reason == "STOP" and t.pnl == -21 and t.pnl_confirmed


def test_reconcile_reopens_wrongly_closed_trade_and_restores_protection(env):
    ex, _ = env
    tid = _open_trade(ex)
    with session_scope() as db:      # simula o erro antigo: marcado fechado, proteções canceladas
        t = db.get(TradeJournal, tid)
        t.status, t.exit_reason, t.exit_time, t.pnl = "CLOSED", "EXTERNAL_CLOSE", utcnow(), 5.0
    ex.orders["sl1"]["status"] = ex.orders["tp1"]["status"] = "CANCELLED"
    monitor.run_monitor()            # concilia: reabre
    with session_scope() as db:
        t = db.get(TradeJournal, tid)
        assert t.status == "OPEN" and t.pnl is None and "reaberto" in t.notes[-1]["text"]
    monitor.run_monitor()            # protege de novo
    kinds = sorted(o["type"] for o in ex.get_open_orders("ZZZ-USDT"))
    assert kinds == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]


def test_reconcile_closes_when_stop_was_passed(env):
    ex, mk = env
    tid = _open_trade(ex)
    with session_scope() as db:
        t = db.get(TradeJournal, tid)
        t.status, t.exit_reason, t.exit_time = "CLOSED", "EXTERNAL_CLOSE", utcnow()
    mk.px = 97.0                     # abaixo do stop (98) de um LONG
    monitor.run_monitor()
    assert ex.positions == []
    with session_scope() as db:
        assert db.get(TradeJournal, tid).exit_reason == "STOP"


def test_foreign_position_gets_emergency_stop(env):
    ex, _ = env
    ex.positions = [{"symbol": "FOO-USDT", "direction": "SHORT", "quantity": 3.0, "entry_price": 100.0,
                     "mark_price": 100.0, "unrealized_pnl": 0.0}]
    monitor.run_monitor()
    stops = [o for o in ex.get_open_orders("FOO-USDT") if o["type"] == "STOP_MARKET"]
    assert len(stops) == 1 and stops[0]["stopPrice"] == 103.0
    assert monitor.broker_state["orphans"][0]["symbol"] == "FOO-USDT"


def test_broker_snapshot_flags_unprotected_position(env, monkeypatch):
    from app.quant import broker
    ex, _ = env
    tid = _open_trade(ex, sym="BBB-USDT")
    ex.orders["sl1"]["status"] = "CANCELLED"                 # stop sumiu
    broker.invalidate()
    s = broker.snapshot()
    row = next(p for p in s["positions"] if p["symbol"] == "BBB-USDT")
    assert row["status"] == "SEM_STOP" and row["trade"]["id"] == tid and s["issues"] >= 1
