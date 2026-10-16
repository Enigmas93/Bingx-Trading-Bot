"""Relatório diário com três seções que NUNCA se misturam: HISTORICAL (backtest), RECENT (backtest 30d) e DEMO."""

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from ..database import session_scope
from ..models import utcnow
from . import events, registry
from .models import DailyReport, MarketRegime, QuantEvent, StrategyPerformance, StrategySettings, TradeJournal

DISCLAIMER = ("Resultados de backtest e da conta DEMO não garantem resultados futuros. Nenhuma estratégia é "
              "considerada lucrativa ou garantida; classificações descrevem apenas o que os dados mostraram até aqui.")


def _scope_table(scope: str) -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(StrategyPerformance).where(StrategyPerformance.scope == scope,
                                                            StrategyPerformance.attribution == "primary")).all()
        out = {}
        for r in rows:
            e = out.setdefault(r.strategy_name, {"strategy": r.strategy_name, "version": r.version})
            e[r.direction] = {"trades": r.executions, "win_rate": r.win_rate, "pf": r.profit_factor,
                              "expectancy_r": r.expectancy, "total_r": r.total_r, "pnl": r.total_pnl,
                              "max_dd_r": r.max_drawdown}
    return sorted(out.values(), key=lambda e: -((e.get("ALL") or {}).get("expectancy_r") or -9))


def generate(day: str | None = None) -> dict:
    if day is None:
        day = (utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    with session_scope() as db:
        closed = db.scalars(select(TradeJournal).where(TradeJournal.exit_time >= start, TradeJournal.exit_time < end,
                                                       TradeJournal.status == "CLOSED")).all()
        opened = db.scalars(select(TradeJournal).where(TradeJournal.created_at >= start,
                                                       TradeJournal.created_at < end)).all()
        still_open = db.scalars(select(TradeJournal).where(TradeJournal.status == "OPEN")).all()
        regimes = db.scalars(select(MarketRegime).where(MarketRegime.created_at >= start,
                                                        MarketRegime.created_at < end)).all()
        evs = db.scalars(select(QuantEvent).where(QuantEvent.created_at >= start, QuantEvent.created_at < end)).all()
        settings = {s.strategy_id: s for s in db.scalars(select(StrategySettings))}
        demo = {
            "opened": len(opened), "closed": len(closed), "open_now": len(still_open),
            "pnl": round(sum(t.pnl or 0 for t in closed), 4), "r": round(sum(t.pnl_r or 0 for t in closed), 2),
            "wins": sum(1 for t in closed if (t.pnl or 0) > 0), "losses": sum(1 for t in closed if (t.pnl or 0) <= 0),
            "pnl_unconfirmed": sum(1 for t in closed if not t.pnl_confirmed),
            "by_strategy": dict(Counter(t.strategy_primary for t in closed)),
            "by_exit_reason": dict(Counter(t.exit_reason for t in closed)),
            "by_direction": {d: round(sum(t.pnl_r or 0 for t in closed if t.direction == d), 2) for d in ("LONG", "SHORT")},
            "trades": [{"uid": t.trade_uid, "symbol": t.symbol, "direction": t.direction, "strategy": t.strategy_primary,
                        "confluence": t.confluence_level, "exit_reason": t.exit_reason, "pnl": t.pnl, "r": t.pnl_r}
                       for t in closed],
        }
        status = [{"strategy": s.strategy_id, "enabled": s.enabled, "health": s.health, "reason": s.health_reason,
                   "tags": s.discovery_tags} for s in settings.values() if registry.BY_ID.get(s.strategy_id)]
    content = {
        "date": day, "disclaimer": DISCLAIMER,
        "DEMO": demo,
        "RECENT": _scope_table("RECENT"),
        "HISTORICAL": _scope_table("HISTORICAL"),
        "regimes": dict(Counter(r.btc_regime for r in regimes)),
        "regime_changes": sum(1 for r in regimes if r.changed),
        "events": dict(Counter(e.event_type for e in evs)),
        "strategies": status,
    }
    with session_scope() as db:   # um relatório por dia: gerar de novo substitui o anterior
        old = db.scalars(select(DailyReport).where(DailyReport.report_date == day)).all()
        if old:
            old[0].content, old[0].created_at = content, utcnow()
            for extra in old[1:]:
                db.delete(extra)
        else:
            db.add(DailyReport(report_date=day, content=content))
    events.emit("REPORT_GENERATED", message=f"relatório diário {day}", date=day)
    return content


def list_reports(limit: int = 30) -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(DailyReport).order_by(DailyReport.id.desc()).limit(limit)).all()
        return [{"id": r.id, "date": r.report_date, "created_at": r.created_at.isoformat(), "content": r.content}
                for r in rows]
