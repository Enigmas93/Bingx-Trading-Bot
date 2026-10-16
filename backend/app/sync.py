"""syncPositions: sincroniza posições da BingX, move o stop para breakeven e apura o resultado real."""

from datetime import timedelta, timezone

from sqlalchemy import select

from . import botlog
from .bingx import BingXClient, BingXError, get_client
from .common import get_config, in_flight_symbols
from .database import session_scope
from .models import Position, Trade, utcnow

MODULE = "sync"
FILLED = "FILLED"
# Campos que o sync é dono (gravados de volta após as chamadas de rede)
POSITION_SYNC_FIELDS = (
    "quantity", "entry_price", "current_price", "unrealized_pnl", "margin", "leverage", "stop_loss",
    "tp1_hit", "breakeven_set", "status", "closed_at", "realized_pnl", "exit_reason",
    "gross_pnl", "fees", "funding", "pnl_confirmed",
)
TRADE_SYNC_FIELDS = ("sl_order_id", "status", "closed_at")


def _ms(dt) -> int:
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _order_status(client: BingXClient, symbol: str, order_id: str | None) -> str | None:
    if not order_id:
        return None
    try:
        return str(client.get_order(symbol, order_id).get("status", "")).upper()
    except BingXError:
        return None


def _move_to_breakeven(client: BingXClient, pos: Position, trade: Trade, mark: float | None, fee_pct: float) -> None:
    """Depois do TP1: novo SL no preço de entrada (+ taxas) para o restante. Cria o novo antes de cancelar o antigo."""
    is_long = pos.direction == "LONG"
    buffer = pos.entry_price * 2 * fee_pct / 100
    be_price = client.round_price(pos.symbol, pos.entry_price + buffer if is_long else pos.entry_price - buffer)
    if mark is not None and ((is_long and be_price >= mark) or (not is_long and be_price <= mark)):
        botlog.info(MODULE, f"{pos.symbol}: preço atual {mark} já passou do breakeven {be_price}; mantendo SL original")
        pos.breakeven_set = True  # não tenta de novo
        return
    try:
        hedge = client.is_hedge_mode()
    except BingXError as exc:
        botlog.warn(MODULE, f"{pos.symbol}: modo de posição indisponível ({exc}); breakeven no próximo sync")
        return
    extra = {} if hedge else {"reduceOnly": "true"}
    try:
        order = client.place_order(
            symbol=pos.symbol, side="SELL" if is_long else "BUY", positionSide=pos.direction if hedge else "BOTH",
            type="STOP_MARKET", quantity=pos.quantity, stopPrice=be_price, workingType="MARK_PRICE", **extra,
        )
    except BingXError as exc:
        botlog.error(MODULE, f"{pos.symbol}: falha ao criar SL de breakeven ({exc}); SL original mantido")
        return
    old_sl = trade.sl_order_id
    trade.sl_order_id = str(order.get("orderId"))
    if old_sl:
        try:
            client.cancel_order(pos.symbol, old_sl)
        except BingXError:
            pass
    pos.stop_loss = be_price
    pos.breakeven_set = True
    botlog.info(MODULE, f"{pos.symbol}: TP1 atingido, stop movido para breakeven @ {be_price}")


def _exit_reason(client: BingXClient, trade: Trade, breakeven_set: bool) -> str:
    st = {name: _order_status(client, trade.symbol, getattr(trade, f"{name}_order_id"))
          for name in ("sl", "tp1", "tp2", "tp3")}
    tps = [n.upper() for n in ("tp1", "tp2", "tp3") if st[n] == FILLED]
    placed = [n for n in ("tp1", "tp2", "tp3") if getattr(trade, f"{n}_order_id")]
    if st["sl"] == FILLED:
        return "+".join(tps + ["BE" if breakeven_set else "SL"])
    if tps and len(tps) == len(placed):
        return "+".join(tps)  # todos os alvos atingidos
    return "+".join(tps + ["MANUAL"])  # fechada fora das ordens do bot


def _fetch_pnl(client: BingXClient, pos: Position) -> dict | None:
    """PnL realizado, taxas e funding da posição na BingX (só rede, sem tocar no banco)."""
    start = _ms(pos.opened_at) - 10_000
    end = _ms(pos.closed_at or utcnow()) + 60_000
    try:
        rows = client.get_income(pos.symbol, start, end)
    except BingXError as exc:
        botlog.warn(MODULE, f"{pos.symbol}: falha ao buscar PnL realizado: {exc}")
        return None
    if not any(r.get("incomeType") == "REALIZED_PNL" for r in rows):
        return None
    gross = sum(float(r["income"]) for r in rows if r.get("incomeType") == "REALIZED_PNL")
    fees = sum(float(r["income"]) for r in rows if r.get("incomeType") == "TRADING_FEE")
    funding = sum(float(r["income"]) for r in rows if r.get("incomeType") == "FUNDING_FEE")
    return {"gross_pnl": round(gross, 6), "fees": round(fees, 6), "funding": round(funding, 6),
            "realized_pnl": round(gross + fees + funding, 6), "pnl_confirmed": True}


def finalize_pnl(client: BingXClient, pos: Position) -> bool:
    """Busca e aplica o PnL real na posição. Retorna True se confirmou."""
    data = _fetch_pnl(client, pos)
    if data is None:
        return False
    for key, value in data.items():
        setattr(pos, key, value)
    return True


def run_sync() -> dict:
    client = get_client()
    if not client.has_credentials:
        return {"skipped": "Credenciais BingX não configuradas"}
    cfg = get_config()

    try:
        exchange = {(p["symbol"], p["direction"]): p for p in client.get_positions()}
    except BingXError as exc:
        botlog.error(MODULE, f"Falha ao buscar posições: {exc}")
        return {"error": str(exc)}

    updated, closed, added, breakevens = 0, 0, 0, 0
    to_cleanup: list[tuple[str, list[str]]] = []

    # Fase 1: leitura rápida. Fase 2: rede (objetos destacados). Fase 3: gravação rápida.
    # Assim o banco não fica travado enquanto a BingX responde.
    with session_scope() as db:
        positions = db.scalars(select(Position).where(Position.status == "open")).all()
        trades = {p.trade_id: db.get(Trade, p.trade_id) for p in positions if p.trade_id}
    # Posições do bot primeiro: se houver duplicata, a do bot é a que casa com a corretora.
    positions = sorted(positions, key=lambda p: (p.source != "bot", p.id))

    trade_before = {tid: {f: getattr(t, f) for f in TRADE_SYNC_FIELDS} for tid, t in trades.items() if t}

    seen: set[tuple[str, str]] = set()
    duplicates: list[Position] = []
    for pos in positions:
        key = (pos.symbol, pos.direction)
        ex = exchange.get(key)
        trade = trades.get(pos.trade_id)
        if ex is not None and key in seen:
            # Duplicata de uma posição que AINDA está aberta na corretora: nunca fechar nem cancelar ordens.
            duplicates.append(pos)
            continue
        if ex is not None:
            seen.add(key)
            pos.quantity = ex["quantity"]
            pos.entry_price = ex["entry_price"] or pos.entry_price
            pos.current_price = ex["mark_price"] or pos.current_price
            pos.unrealized_pnl = ex["unrealized_pnl"]
            pos.margin = ex["margin"]
            pos.leverage = ex["leverage"] or pos.leverage
            updated += 1

            # TP1 atingido? -> breakeven no restante
            if trade and not pos.tp1_hit and _order_status(client, pos.symbol, trade.tp1_order_id) == FILLED:
                pos.tp1_hit = True
            if trade and pos.tp1_hit and not pos.breakeven_set and cfg.breakeven_after_tp1:
                _move_to_breakeven(client, pos, trade, ex["mark_price"], cfg.fee_rate_pct)
                breakevens += pos.breakeven_set
            continue

        # Fechada na BingX -> fecha no banco com motivo e PnL real
        pos.status = "closed"
        pos.closed_at = utcnow()
        pos.realized_pnl = pos.unrealized_pnl  # estimativa até confirmar pela BingX
        closed += 1
        if trade:
            pos.exit_reason = _exit_reason(client, trade, pos.breakeven_set)
            if trade.status == "open":
                trade.status = "closed"
                trade.closed_at = utcnow()
                ids = [i for i in (trade.sl_order_id, trade.tp1_order_id, trade.tp2_order_id,
                                   trade.tp3_order_id) if i]
                to_cleanup.append((trade.symbol, ids))
        else:
            pos.exit_reason = "EXTERNA"
        if pos.source == "bot":
            finalize_pnl(client, pos)
        botlog.info(MODULE, f"{pos.symbol} {pos.direction}: fechada ({pos.exit_reason}) PnL "
                            f"{pos.realized_pnl:.4f} {'(confirmado)' if pos.pnl_confirmed else '(estimado)'}")

    # Posições abertas manualmente/fora do bot (exceto as que o executor está abrindo agora)
    busy = in_flight_symbols()
    # Posições do motor quant são acompanhadas pelo diário (quant/monitor.py), não como "externas".
    from .quant.monitor import open_trade_symbols
    busy |= open_trade_symbols()
    new_external = [ex for key, ex in exchange.items() if key not in seen and ex["symbol"] not in busy]

    removed = 0
    with session_scope() as db:
        duplicate_ids = {d.id for d in duplicates}
        for pos in positions:
            if pos.id in duplicate_ids:
                continue
            row = db.get(Position, pos.id)
            for f in POSITION_SYNC_FIELDS:
                setattr(row, f, getattr(pos, f))
        for dup in duplicates:
            if dup.source == "external":  # registro fantasma criado durante a abertura pelo bot
                db.delete(db.get(Position, dup.id))
                removed += 1
            else:
                botlog.warn(MODULE, f"{dup.symbol}: posição #{dup.id} do bot duplicada no banco; mantida sem alterações")
        for tid, trade in trades.items():
            if trade is None:
                continue
            changed = {f: getattr(trade, f) for f in TRADE_SYNC_FIELDS if getattr(trade, f) != trade_before[tid][f]}
            if changed:  # só o que o sync alterou (não sobrescreve TP2/TP3 gravados em paralelo)
                row = db.get(Trade, tid)
                for f, value in changed.items():
                    setattr(row, f, value)
        for ex in new_external:
            already = db.scalars(select(Position).where(
                Position.status == "open", Position.symbol == ex["symbol"], Position.direction == ex["direction"]
            )).first()
            if already is not None:  # gravada pelo executor enquanto o sync consultava a corretora
                continue
            db.add(Position(
                symbol=ex["symbol"], direction=ex["direction"], quantity=ex["quantity"],
                entry_price=ex["entry_price"], current_price=ex["mark_price"],
                unrealized_pnl=ex["unrealized_pnl"], margin=ex["margin"], leverage=ex["leverage"],
                protected=False, source="external",
            ))
            added += 1

    # Cancela SL/TP remanescentes de posições já fechadas
    for symbol, ids in to_cleanup:
        for order_id in ids:
            try:
                client.cancel_order(symbol, order_id)
            except BingXError:
                pass  # já executada/cancelada

    summary = {"updated": updated, "closed": closed, "external_added": added, "breakeven": breakevens,
               "duplicates_removed": removed}
    if closed or added or breakevens or removed:
        botlog.info(MODULE, f"Sync: {summary}")
    return summary


def confirm_pending_pnl(max_age_days: int = 7) -> int:
    """Tenta confirmar o PnL real (e o motivo de saída) das posições fechadas que ainda estão como estimativa."""
    client = get_client()
    if not client.has_credentials:
        return 0
    since = utcnow() - timedelta(days=max_age_days)
    confirmed = 0
    # Lê tudo numa transação curta; as chamadas de rede acontecem FORA do banco (não trava os outros jobs).
    with session_scope() as db:
        rows = db.scalars(select(Position).where(
            Position.source == "bot", Position.status == "closed", Position.pnl_confirmed.is_(False))).all()
        trades = {p.trade_id: db.get(Trade, p.trade_id) for p in rows if p.trade_id}
    for pos in rows:
        closed_at = pos.closed_at if pos.closed_at.tzinfo else pos.closed_at.replace(tzinfo=timezone.utc)
        if closed_at < since:
            continue
        exit_reason = pos.exit_reason
        trade = trades.get(pos.trade_id)
        if exit_reason is None and trade:
            exit_reason = _exit_reason(client, trade, pos.breakeven_set)
        data = _fetch_pnl(client, pos)
        with session_scope() as db:
            row = db.get(Position, pos.id)
            row.exit_reason = exit_reason
            for key, value in (data or {}).items():
                setattr(row, key, value)
        confirmed += data is not None
    if confirmed:
        botlog.info(MODULE, f"PnL real confirmado em {confirmed} posição(ões)")
    return confirmed
