"""Relatório de desempenho a partir das posições fechadas pelo bot."""

from collections import defaultdict
from datetime import timedelta

from sqlalchemy import select

from .database import session_scope
from .models import Position, Signal, Trade, utcnow


def _stats(rows: list[dict]) -> dict:
    pnls = [r["pnl"] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    rs = [r["r"] for r in rows if r["r"] is not None]
    gross_loss = abs(sum(losses))
    return {
        "trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else None,
        "net_pnl": round(sum(pnls), 4),
        "avg_win": round(sum(wins) / len(wins), 4) if wins else None,
        "avg_loss": round(sum(losses) / len(losses), 4) if losses else None,
        "profit_factor": round(sum(wins) / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(sum(pnls) / len(pnls), 4) if pnls else None,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else None,
    }


def _bucket(value: float | None, edges: list[tuple[float, str]]) -> str:
    if value is None:
        return "?"
    label = edges[0][1]
    for edge, name in edges:
        if value >= edge:
            label = name
    return label


SCORE_EDGES = [(0, "<80"), (80, "80-99"), (100, "100-119"), (120, "120+")]
ADX_EDGES = [(0, "18-22"), (22, "22-30"), (30, "30-40"), (40, "40+")]


def build_report(days: int | None = None) -> dict:
    since = utcnow() - timedelta(days=days) if days else None
    with session_scope() as db:
        query = select(Position).where(Position.source == "bot", Position.status == "closed") \
            .order_by(Position.closed_at)
        positions = db.scalars(query).all()
        rows = []
        for p in positions:
            closed = p.closed_at if p.closed_at.tzinfo else p.closed_at.replace(tzinfo=utcnow().tzinfo)
            if since and closed < since:
                continue
            trade = db.get(Trade, p.trade_id) if p.trade_id else None
            sig = db.get(Signal, trade.signal_id) if trade and trade.signal_id else None
            pnl = p.realized_pnl or 0.0
            rows.append({
                "symbol": p.symbol, "direction": p.direction, "pnl": pnl,
                "r": round(pnl / p.risk_amount, 3) if p.risk_amount else None,
                "confirmed": p.pnl_confirmed, "exit_reason": p.exit_reason or "?",
                "score": sig.confidence_score if sig else None, "adx": sig.adx if sig else None,
                "news": (sig.news_result or {}).get("impact", "sem checagem") if sig else "?",
                "closed_at": closed.isoformat(),
            })

        shadow = defaultdict(int)
        for sig in db.scalars(select(Signal).where(Signal.status == "cancelled", Signal.news_result.is_not(None))):
            news = sig.news_result or {}
            if not news.get("allow", True):
                shadow["cancelados"] += 1
                shadow[news.get("shadow") or "pendente"] += 1

    def group(key) -> list[dict]:
        groups: dict[str, list] = defaultdict(list)
        for r in rows:
            groups[key(r)].append(r)
        return sorted(({"group": k, **_stats(v)} for k, v in groups.items()), key=lambda g: -g["trades"])

    equity, peak, max_dd, curve = 0.0, 0.0, 0.0, []
    for r in rows:
        equity += r["pnl"]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        curve.append({"t": r["closed_at"], "equity": round(equity, 4)})

    return {
        "summary": {**_stats(rows), "max_drawdown": round(max_dd, 4),
                    "confirmed": sum(1 for r in rows if r["confirmed"]),
                    "estimated": sum(1 for r in rows if not r["confirmed"])},
        "equity_curve": curve,
        "by_direction": group(lambda r: r["direction"]),
        "by_exit_reason": group(lambda r: r["exit_reason"]),
        "by_score": group(lambda r: _bucket(r["score"], SCORE_EDGES)),
        "by_adx": group(lambda r: _bucket(r["adx"], ADX_EDGES)),
        "by_news": group(lambda r: r["news"]),
        "by_symbol": group(lambda r: r["symbol"])[:15],
        "news_shadow": dict(shadow),
    }
