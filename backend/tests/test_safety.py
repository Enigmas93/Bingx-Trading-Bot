"""Nenhuma execução real é possível; análise sempre com dados da conta real (somente leitura)."""

import subprocess
import sys
from pathlib import Path

import pytest

from app import settings
from app.bingx import DEMO_URL, LIVE_URL, BingXClient, BingXError
from app.quant import data, safety
from app.quant.execution import DemoExecutionAdapter, LiveExecutionAdapter, get_adapter


@pytest.fixture(autouse=True)
def demo_defaults(monkeypatch):
    monkeypatch.setattr(settings, "DEMO_MODE", True)
    monkeypatch.setattr(settings, "LIVE_TRADING", False)
    monkeypatch.setattr(settings, "BINGX_DEMO", True)


def test_demo_config_is_ready():
    st = safety.check_demo_safety(BingXClient("k", "s", demo=True))
    assert st["execution_mode"] == "DEMO_ONLY" and st["live_execution"] == "BLOCKED"


@pytest.mark.parametrize("attr,value", [("LIVE_TRADING", True), ("DEMO_MODE", False), ("BINGX_DEMO", False)])
def test_incompatible_config_locks_engine(monkeypatch, attr, value):
    monkeypatch.setattr(settings, attr, value)
    st = safety.check_demo_safety(BingXClient("k", "s", demo=True))
    assert not st["safety_ok"] and st["engine_state"] == "LOCKED"
    with pytest.raises(safety.LiveExecutionBlocked, match="ABORT EXECUTION"):
        safety.assert_demo()
    with pytest.raises(safety.LiveExecutionBlocked):
        DemoExecutionAdapter()


def test_live_client_refuses_orders_before_network():
    live = BingXClient("k", "s", demo=False)
    for call in (lambda: live.place_order(symbol="BTC-USDT", side="BUY", type="MARKET", quantity=1),
                 lambda: live.set_leverage("BTC-USDT", "BOTH", 5),
                 lambda: live.set_margin_type("BTC-USDT"),
                 lambda: live.cancel_order("BTC-USDT", "1")):
        with pytest.raises(BingXError) as exc:
            call()
        assert exc.value.code == "LIVE_EXECUTION_BLOCKED"


def test_demo_client_refuses_orders_when_live_flag(monkeypatch):
    monkeypatch.setattr(settings, "LIVE_TRADING", True)
    demo = BingXClient("k", "s", demo=True)
    with pytest.raises(BingXError) as exc:
        demo.place_order(symbol="BTC-USDT", side="BUY", type="MARKET", quantity=1)
    assert exc.value.code == "LIVE_EXECUTION_BLOCKED"


def test_live_adapter_always_blocked():
    with pytest.raises(safety.LiveExecutionBlocked):
        LiveExecutionAdapter().open_trade({}, {}, {})
    with pytest.raises(safety.LiveExecutionBlocked):
        LiveExecutionAdapter().close_trade(1, "X", "y")


def test_get_adapter_is_demo():
    assert get_adapter().mode == "DEMO"
    assert get_adapter().client.base_url == DEMO_URL


def test_analysis_data_is_live_read_only():
    mk = data.market()
    assert mk.base_url == LIVE_URL and mk.read_only and not mk.has_credentials
    with pytest.raises(BingXError) as exc:
        mk.get_balance()   # requisição assinada: recusada sem tocar na rede
    assert exc.value.code == "READ_ONLY_CLIENT"
    with pytest.raises(BingXError):
        mk.place_order(symbol="BTC-USDT", side="BUY", type="MARKET", quantity=1)


def test_research_never_imports_execution():
    code = ("import sys; import app.quant.research; "
            "bad=[m for m in sys.modules if m in ('app.quant.execution','app.executor','app.auto_execute')]; "
            "print(bad); sys.exit(1 if bad else 0)")
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                       env={**__import__("os").environ})
    assert r.returncode == 0, r.stdout + r.stderr


def test_no_api_route_switches_to_live():
    from app.main import app
    paths = " ".join(app.openapi()["paths"]).lower()
    assert "/api/quant/overview" in paths
    assert "live" not in paths
    from app.quant.api import QuantConfigUpdate
    assert not any(k in QuantConfigUpdate.model_fields for k in ("live_trading", "demo_mode", "execution_mode"))


def test_config_update_ignores_read_only_but_rejects_live():
    import pydantic
    from app.quant.api import QuantConfigUpdate
    m = QuantConfigUpdate(**{"id": 1, "updated_at": "x", "risk_reset_at": "x", "block_timezone": "x",
                             "single_signal_min_score": 60})
    assert m.model_dump(exclude_none=True) == {"single_signal_min_score": 60}
    with pytest.raises(pydantic.ValidationError):
        QuantConfigUpdate(live_trading=True)


def test_api_token_required_when_configured(monkeypatch):
    from fastapi.testclient import TestClient
    from app import settings as st
    from app.main import app
    monkeypatch.setattr(st, "BOT_API_TOKEN", "segredo-123")
    c = TestClient(app)   # sem lifespan (não inicia o agendador)
    assert c.get("/api/quant/safety").status_code == 401
    assert c.get("/api/quant/safety", headers={"X-Bot-Token": "errado"}).status_code == 401
    assert c.get("/api/quant/safety", headers={"X-Bot-Token": "segredo-123"}).status_code == 200
    monkeypatch.setattr(st, "BOT_API_TOKEN", "")
    assert c.get("/api/quant/safety").status_code == 200
