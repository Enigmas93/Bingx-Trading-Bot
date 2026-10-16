"""Log estruturado do sistema quantitativo (tabela quant_events + log do bot)."""

from .. import botlog
from ..database import session_scope
from .models import QuantEvent

EVENT_TYPES = {
    "SIGNAL_GENERATED", "OPPORTUNITY_RANKED", "TRADE_OPENED", "TRADE_UPDATED", "TRADE_CLOSED",
    "STRATEGY_ENABLED", "STRATEGY_DISABLED", "STRATEGY_UPDATED", "REGIME_CHANGED",
    "LIVE_CONFIGURATION_DETECTED", "RESEARCH_FINISHED", "REPORT_GENERATED", "ERROR",
}

_LEVEL = {"ERROR": "ERROR", "LIVE_CONFIGURATION_DETECTED": "CRITICAL"}


def emit(event_type: str, symbol: str | None = None, strategy: str | None = None,
         trade_id: int | None = None, log: bool = True, **details) -> None:
    """Grava um evento estruturado. Nunca lança exceção (log não pode derrubar o motor)."""
    try:
        with session_scope() as db:
            db.add(QuantEvent(event_type=event_type, symbol=symbol, strategy=strategy, trade_id=trade_id,
                              details=_clean(details)))
    except Exception as exc:  # pragma: no cover
        botlog.error("quant", f"Falha ao gravar evento {event_type}: {exc}")
    if log:
        parts = [event_type] + [p for p in (symbol, strategy, f"trade#{trade_id}" if trade_id else None) if p]
        msg = details.get("message") or ""
        botlog.log("quant", _LEVEL.get(event_type, "INFO"), " | ".join(parts) + (f" — {msg}" if msg else ""))


def _clean(value):
    """JSON-safe (números numpy, conjuntos, etc.)."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_clean(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return str(value)
    if isinstance(value, float) and value != value:  # NaN
        return None
    return value
