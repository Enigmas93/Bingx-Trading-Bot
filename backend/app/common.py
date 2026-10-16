import threading
from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import select

from . import botlog
from .bingx import BingXClient
from .database import session_scope
from .models import BotConfig, Position, Signal, utcnow


_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


@contextmanager
def executing(symbol: str):
    """Marca o par como 'abrindo posição' até ela estar gravada no banco (o sync não a toma por externa)."""
    with _in_flight_lock:
        _in_flight.add(symbol)
    try:
        yield
    finally:
        with _in_flight_lock:
            _in_flight.discard(symbol)


def in_flight_symbols() -> set[str]:
    with _in_flight_lock:
        return set(_in_flight)


def get_config() -> BotConfig:
    with session_scope() as db:
        return db.get(BotConfig, 1)


def open_position_keys(client: BingXClient) -> set[tuple[str, str]]:
    """(símbolo, direção) de todas as posições abertas: corretora + banco."""
    keys: set[tuple[str, str]] = set()
    with session_scope() as db:
        for p in db.scalars(select(Position).where(Position.status == "open")):
            keys.add((p.symbol, p.direction))
    if client.has_credentials:
        try:
            for p in client.get_positions():
                keys.add((p["symbol"], p["direction"]))
        except Exception as exc:
            botlog.warn("common", f"Não foi possível consultar posições na BingX: {exc}")
    return keys


def open_symbols(client: BingXClient) -> set[str]:
    return {symbol for symbol, _ in open_position_keys(client)}


def expire_stale_signals(cfg: BotConfig) -> int:
    """Sinais 'approved' mais antigos que signal_expiry_minutes viram 'expired'."""
    limit = utcnow() - timedelta(minutes=cfg.signal_expiry_minutes)
    count = 0
    with session_scope() as db:
        for sig in db.scalars(select(Signal).where(Signal.status == "approved")):
            created = sig.created_at if sig.created_at.tzinfo else sig.created_at.replace(tzinfo=limit.tzinfo)
            if created < limit:
                sig.status = "expired"
                sig.status_reason = f"Sinal expirou após {cfg.signal_expiry_minutes} min sem execução"
                count += 1
    return count


def is_blocked_hour(cfg: BotConfig) -> bool:
    if not cfg.block_hours_enabled:
        return False
    hour = utcnow().hour
    start, end = cfg.block_hour_start, cfg.block_hour_end
    if start <= end:
        return start <= hour <= end
    return hour >= start or hour <= end
