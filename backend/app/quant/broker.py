"""Visão da CORRETORA (conta de execução): posições, ordens, saldo e resultado realizado, direto da BingX.

O painel mostra estes números como fonte da verdade e os compara com o diário. Assim qualquer divergência (posição sem
trade, trade sem posição, posição sem stop, resultado diferente) fica visível em vez de silenciosa.
"""

import threading
import time
from collections import defaultdict
from datetime import timezone

from sqlalchemy import func, select

from ..bingx import BingXError
from ..database import session_scope
from ..models import utcnow
from . import data
from .models import TradeJournal

_cache: dict = {}
_lock = threading.Lock()
SNAPSHOT_TTL = 15          # s: posições/ordens/saldo
RESULT_TTL = 300           # s: soma dos lançamentos (consulta mais pesada)


def _cached(key: str, ttl: int, fn):
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def snapshot() -> dict:
    """Posições e ordens abertas na corretora, cruzadas com os trades OPEN do diário."""
    def build():
        c = data.execution()
        try:
            positions = c.get_positions()
            orders = c.get_open_orders()
            balance = c.get_balance()
        except BingXError as exc:
            return {"ok": False, "error": str(exc), "checked_at": utcnow().isoformat()}
        by_sym = defaultdict(list)
        for o in orders:
            by_sym[o.get("symbol")].append(o)
        with session_scope() as db:
            open_rows = db.scalars(select(TradeJournal).where(TradeJournal.status == "OPEN")).all()
            journal = {(t.symbol, t.direction): {"id": t.id, "uid": t.trade_uid, "strategy": t.strategy_primary,
                                                 "timeframe": t.timeframe, "stop": t.current_stop or t.stop_reference,
                                                 "target": t.target_reference, "entry_time": t.entry_time.isoformat()
                                                 if t.entry_time else None} for t in open_rows}
        rows = []
        for p in positions:
            key = (p["symbol"], p["direction"])
            ords = by_sym.get(p["symbol"], [])
            stop = next((float(o.get("stopPrice") or 0) for o in ords if o.get("type") == "STOP_MARKET"), None)
            tgt = next((float(o.get("stopPrice") or 0) for o in ords if o.get("type") == "TAKE_PROFIT_MARKET"), None)
            j = journal.get(key)
            status = "OK" if j and stop else ("SEM_STOP" if not stop else "SEM_TRADE")
            rows.append({**p, "stop_order": stop, "target_order": tgt, "trade": j, "status": status,
                         "notional": round(p["quantity"] * (p.get("mark_price") or p["entry_price"]), 4)})
        missing = [{"symbol": s, "direction": d, "trade_id": j["id"]} for (s, d), j in journal.items()
                   if (s, d) not in {(p["symbol"], p["direction"]) for p in positions}]
        return {"ok": True, "checked_at": utcnow().isoformat(), "balance": balance, "positions": rows,
                "trades_without_position": missing,
                "unrealized_pnl": round(sum(p.get("unrealized_pnl") or 0 for p in positions), 4),
                "issues": sum(1 for r in rows if r["status"] != "OK") + len(missing)}
    return _cached("snapshot", SNAPSHOT_TTL, build)


def _income_since(start_ms: int) -> dict:
    c = data.execution()
    end = int(time.time() * 1000)
    rows, s = [], start_ms
    while s < end:
        e = min(s + 7 * 86_400_000, end)
        page_end = e
        while True:
            r = c._request("GET", "/openApi/swap/v2/user/income",
                           {"startTime": s, "endTime": page_end, "limit": 1000}, signed=True) or []
            rows += r
            if len(r) < 1000:
                break
            page_end = min(int(x["time"]) for x in r) - 1
        s = e
    seen, agg = set(), defaultdict(float)
    for x in rows:
        k = (x.get("tranId"), x.get("time"), x.get("symbol"), x.get("incomeType"), x.get("income"))
        if k in seen:
            continue
        seen.add(k)
        agg[x.get("incomeType")] += float(x.get("income") or 0)
    return {k: round(v, 4) for k, v in agg.items()}


def result() -> dict:
    """Resultado realizado REAL (lançamentos da corretora) desde o 1º trade do motor, comparado com o diário."""
    def build():
        with session_scope() as db:
            first = db.scalar(select(func.min(TradeJournal.entry_time)))
            closed = db.execute(select(func.count(), func.sum(TradeJournal.pnl), func.sum(TradeJournal.pnl_r))
                                .where(TradeJournal.status == "CLOSED", TradeJournal.pnl_confirmed.is_(True))).one()
            unconf = db.execute(select(func.count(), func.sum(TradeJournal.pnl))
                                .where(TradeJournal.status == "CLOSED", TradeJournal.pnl_confirmed.is_(False))).one()
        if first is None:
            return {"ok": True, "since": None, "broker": {}, "broker_net": 0.0}
        start = int((first if first.tzinfo else first.replace(tzinfo=timezone.utc)).timestamp() * 1000) - 60_000
        try:
            agg = _income_since(start)
        except BingXError as exc:
            return {"ok": False, "error": str(exc)}
        net = round(sum(v for k, v in agg.items() if k in ("REALIZED_PNL", "TRADING_FEE", "FUNDING_FEE")), 4)
        jnet = round(closed[1] or 0, 4)
        explained = _explained(start)
        unexplained = round(jnet - net + explained["open_positions_fees"] + explained["legacy_positions"], 4)
        return {"ok": True, "since": first.isoformat(), "broker": agg, "broker_net": net,
                "journal_net": jnet, "journal_trades": closed[0], "journal_r": round(closed[2] or 0, 2),
                "unconfirmed_trades": unconf[0], "unconfirmed_pnl": round(unconf[1] or 0, 4),
                "difference": round(jnet - net, 4), "explained": explained, "unexplained": unexplained,
                "checked_at": utcnow().isoformat()}
    return _cached("result", RESULT_TTL, build)


def _explained(start_ms: int) -> dict:
    """Partes conhecidas da diferença diário × corretora:
    - taxas/funding já cobrados de posições AINDA abertas (o diário só soma trades fechados);
    - posições do sistema anterior fechadas depois do início do motor (não pertencem ao diário)."""
    from ..models import Position
    c = data.execution()
    ms = lambda d: int((d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp() * 1000)  # noqa: E731
    open_fees = legacy = 0.0
    with session_scope() as db:
        opened = [(t.symbol, t.entry_time) for t in db.scalars(select(TradeJournal).where(TradeJournal.status == "OPEN"))]
        old = [(p.symbol, p.closed_at) for p in db.scalars(select(Position).where(Position.closed_at.is_not(None),
                                                                         Position.source == "bot"))
               if ms(p.closed_at) >= start_ms]
    try:
        for sym, et in opened:
            open_fees += sum(float(r["income"]) for r in c.get_income(sym, ms(et) - 90_000, None)
                             if r.get("incomeType") in ("TRADING_FEE", "FUNDING_FEE"))
        for sym, ct in old:
            legacy += sum(float(r["income"]) for r in c.get_income(sym, ms(ct) - 120_000, ms(ct) + 120_000)
                          if r.get("incomeType") in ("REALIZED_PNL", "TRADING_FEE", "FUNDING_FEE"))
    except BingXError:
        pass
    return {"open_positions_fees": round(open_fees, 4), "legacy_positions": round(legacy, 4)}


def invalidate() -> None:
    with _lock:
        _cache.clear()
