"""Proteção DEMO ONLY.

Esta versão do sistema é exclusivamente para pesquisa e validação. Regras:
- DEMO_MODE precisa ser true e LIVE_TRADING precisa ser false (.env);
- o cliente de execução precisa apontar para a conta DEMO (VST) da BingX;
- não existe caminho de interface ou API para mudar para conta real.
Se algo estiver incompatível, o motor fica TRAVADO: nenhuma ordem é enviada e o evento
LIVE_CONFIGURATION_DETECTED é registrado. Uma segunda trava existe no próprio cliente BingX
(BingXClient._assert_execution_allowed), que recusa ordens fora da DEMO para qualquer código.
"""

import threading

from .. import settings
from ..bingx import DEMO_URL, get_client
from . import events

_state = {"checked": False, "ok": False, "reasons": [], "reported": False}
_lock = threading.Lock()


class LiveExecutionBlocked(RuntimeError):
    pass


def check_demo_safety(client=None) -> dict:
    client = client or get_client()
    reasons = []
    if not settings.DEMO_MODE:
        reasons.append("DEMO_MODE não é true")
    if settings.LIVE_TRADING:
        reasons.append("LIVE_TRADING está true")
    if not settings.BINGX_DEMO:
        reasons.append("BINGX_TESTNET/BINGX_DEMO não é true (cliente apontaria para a conta real)")
    if client.base_url != DEMO_URL:
        reasons.append(f"cliente de execução aponta para {client.base_url} (esperado {DEMO_URL})")
    ok = not reasons
    with _lock:
        _state.update(checked=True, ok=ok, reasons=reasons)
        first_report = not ok and not _state["reported"]
        if first_report:
            _state["reported"] = True
    if first_report:
        events.emit("LIVE_CONFIGURATION_DETECTED", message="Execução abortada: configuração incompatível com DEMO",
                    reasons=reasons)
    return status()


def status() -> dict:
    with _lock:
        ok = _state["ok"]
        return {
            "execution_mode": "DEMO_ONLY",
            "live_execution": "BLOCKED",
            "safety_ok": ok,
            "engine_state": "READY" if ok else "LOCKED",
            "reasons": list(_state["reasons"]),
            "demo_mode": settings.DEMO_MODE,
            "live_trading": settings.LIVE_TRADING,
            "account": "DEMO (VST)" if settings.BINGX_DEMO else "REAL",
            "execution_endpoint": get_client().base_url,
        }


def assert_demo() -> None:
    """Chamado antes de QUALQUER ação de execução. Lança LiveExecutionBlocked se não for DEMO."""
    st = check_demo_safety()
    if not st["safety_ok"]:
        raise LiveExecutionBlocked("ABORT EXECUTION: " + "; ".join(st["reasons"]))
