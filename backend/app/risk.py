"""Controles de risco: limite diário, perdas seguidas, cooldown por par e exposição por direção."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .database import session_scope
from .models import BotConfig, Position, utcnow


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _closed_bot_positions_since(since: datetime) -> list[Position]:
    with session_scope() as db:
        rows = db.scalars(
            select(Position).where(Position.source == "bot", Position.status == "closed")
            .order_by(Position.closed_at.desc())
        ).all()
    return [p for p in rows if _aware(p.closed_at) and _aware(p.closed_at) >= since]


def risk_state(cfg: BotConfig, equity: float | None) -> dict:
    """paused=True quando o limite diário de perda ou de perdas seguidas foi atingido (reinicia 00:00 UTC)."""
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today = _closed_bot_positions_since(day_start)
    daily_pnl = sum(p.realized_pnl or 0.0 for p in today)

    consecutive = 0
    for p in today:  # mais recente primeiro
        if (p.realized_pnl or 0.0) < 0:
            consecutive += 1
        else:
            break

    state = {"paused": False, "reason": None, "daily_pnl": round(daily_pnl, 4),
             "consecutive_losses": consecutive, "trades_today": len(today)}
    if equity and equity > 0 and cfg.daily_loss_limit_pct > 0:
        start_equity = equity - daily_pnl
        limit = start_equity * cfg.daily_loss_limit_pct / 100
        state["daily_loss_limit"] = round(limit, 4)
        if daily_pnl <= -limit:
            state.update(paused=True, reason=f"Limite de perda diária atingido ({daily_pnl:.2f} <= -{limit:.2f})")
    if not state["paused"] and cfg.max_consecutive_losses > 0 and consecutive >= cfg.max_consecutive_losses:
        state.update(paused=True, reason=f"{consecutive} perdas seguidas hoje (limite {cfg.max_consecutive_losses})")
    return state


def cooldown_symbols(cfg: BotConfig) -> dict[str, datetime]:
    """Pares que fecharam no prejuízo há menos de symbol_cooldown_minutes -> liberação."""
    if cfg.symbol_cooldown_minutes <= 0:
        return {}
    since = utcnow() - timedelta(minutes=cfg.symbol_cooldown_minutes)
    out: dict[str, datetime] = {}
    for p in _closed_bot_positions_since(since):
        if (p.realized_pnl or 0.0) < 0:
            until = _aware(p.closed_at) + timedelta(minutes=cfg.symbol_cooldown_minutes)
            out[p.symbol] = max(out.get(p.symbol, until), until)
    return out
