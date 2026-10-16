"""Tarefas periódicas de apuração: PnL real pendente e resultado 'sombra' dos sinais cancelados por notícia."""

from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from . import botlog
from .bingx import get_client
from .database import session_scope
from .models import Signal, utcnow
from .news import shadow_outcome
from .sync import confirm_pending_pnl

MODULE = "maintenance"
SHADOW_MIN_AGE = timedelta(hours=1)
SHADOW_MAX_PER_RUN = 15


def _evaluate_shadows() -> int:
    client = get_client()

    def loader(symbol: str, start_ms: int) -> list[dict]:
        candles = client.get_klines(symbol, "5m", 300, start_ms=start_ms)
        return [k for k in candles if k["time"] >= start_ms]

    now = utcnow()
    done = 0
    with session_scope() as db:
        rows = db.scalars(select(Signal).where(Signal.status == "cancelled", Signal.news_result.is_not(None))).all()
    for sig in rows:
        news = dict(sig.news_result or {})
        if news.get("allow", True) or news.get("shadow") in ("win", "loss", "open"):
            continue
        created = sig.created_at if sig.created_at.tzinfo else sig.created_at.replace(tzinfo=timezone.utc)
        if now - created < SHADOW_MIN_AGE:
            continue
        try:
            outcome = shadow_outcome(sig, loader)  # rede fora da transação
        except Exception as exc:
            botlog.warn(MODULE, f"{sig.symbol}: falha na avaliação sombra: {exc}")
            continue
        if outcome is None:
            continue
        news["shadow"] = outcome
        with session_scope() as db:
            row = db.get(Signal, sig.id)
            row.news_result = news
            flag_modified(row, "news_result")
        done += 1
        if done >= SHADOW_MAX_PER_RUN:
            break
    return done


def run_maintenance() -> dict:
    confirmed = confirm_pending_pnl()
    shadows = _evaluate_shadows()
    if shadows:
        botlog.info(MODULE, f"Resultado sombra calculado para {shadows} sinal(is) cancelado(s) por notícia")
    return {"pnl_confirmado": confirmed, "sombras": shadows}
