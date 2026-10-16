"""Monitor de trades DEMO (roda junto do sync, a cada 30 s).

Para cada trade OPEN do diário:
- confirma que a posição existe na DEMO e que o stop está ativo (recria se sumir);
- atualiza preço/PnL não realizado, melhor/pior preço (dados da conta REAL);
- move o stop para o breakeven (+ taxas) quando o lucro atinge `breakeven_at_r` R;
- quando a posição some da corretora: identifica o motivo (STOP/TARGET/saída do motor/externo), busca o PnL REAL
  realizado (income da conta DEMO: PnL bruto, taxas, funding), calcula R, MFE/MAE (candles 5m da conta real) e
  fecha o registro. Nada é apagado.
"""

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import botlog
from ..bingx import BingXError
from ..common import in_flight_symbols
from ..database import session_scope
from ..models import utcnow
from . import data, events
from .models import DemoPosition, QuantConfig, TradeJournal
from .safety import check_demo_safety

MODULE = "quant.monitor"
FEE_PCT = 0.05  # taxa taker por lado (%) para o breakeven cobrir os custos

# Confirmação de fechamento. Uma ausência na lista de posições NÃO basta: a API às vezes devolve a lista vazia
# (incidentes de 09/10, 15/10 e 16/10, quando posições abertas ficaram sem stop). O trade só é finalizado quando:
#   1) some em MISS_CONFIRM ciclos seguidos (30 s cada) E a consulta específica do par também não o encontra; e
#   2) a corretora mostra evidência do fechamento (stop/alvo/ordem de fechamento executados ou PnL realizado),
#      ou ele segue ausente por MISS_NO_EVIDENCE ciclos (fechado fora do motor).
MISS_CONFIRM = 2
MISS_NO_EVIDENCE = 6
_misses: dict[int, int] = {}


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ms(dt) -> int:
    return int(_aware(dt).timestamp() * 1000)


def open_trades() -> list[dict]:
    with session_scope() as db:
        rows = db.scalars(select(TradeJournal).where(TradeJournal.status == "OPEN")).all()
        return [{c.name: getattr(r, c.name) for c in TradeJournal.__table__.columns} for r in rows]


def open_trade_symbols() -> set[str]:
    return {t["symbol"] for t in open_trades()}


def run_monitor() -> dict:
    trades = open_trades()
    st = check_demo_safety()
    if not trades:
        out = {"open": 0, "pnl_confirmed_late": confirm_pending(), "reclassified": reclassify_external()}
        if st["safety_ok"]:
            from .execution import get_adapter
            try:
                out["reconcile"] = reconcile(get_adapter())
            except BingXError as exc:
                out["reconcile"] = {"error": str(exc)}
        return out
    if not st["safety_ok"]:
        return {"skipped": "configuração não-DEMO: monitor não envia ordens", "reasons": st["reasons"]}
    from .execution import get_adapter
    adapter = get_adapter()
    client = adapter.client
    with session_scope() as db:
        qc = db.get(QuantConfig, 1)
        be_r = qc.breakeven_at_r if qc else 1.0
    try:
        positions = client.get_positions()
        if not positions:   # lista vazia com trades abertos: suspeito, consulta de novo antes de concluir
            time.sleep(2)
            positions = client.get_positions()
    except BingXError as exc:
        botlog.warn(MODULE, f"não foi possível ler posições da DEMO: {exc}")
        return {"error": str(exc)}
    live = {(p["symbol"], p["direction"]): p for p in positions}
    busy = in_flight_symbols()
    out = {"open": len(trades), "closed": 0, "breakeven": 0, "stop_recreated": 0, "target_recreated": 0,
           "missing": 0}

    for t in trades:
        key = (t["symbol"], t["direction"])
        if t["symbol"] in busy:
            continue
        pos = live.get(key)
        if pos is None:
            pos = _position_of(client, t)       # confirma com a consulta específica do par
        if pos is None:
            out["missing"] += 1
            if _confirm_closed(adapter, t) and _finalize(adapter, t):
                out["closed"] += 1
            continue
        _misses.pop(t["id"], None)
        if pos.get("unknown"):
            continue
        try:
            price = data.market().get_price(t["symbol"])   # conta real
        except BingXError:
            price = pos.get("mark_price") or t["entry_price"]
        sign = 1 if t["direction"] == "LONG" else -1
        r_unit = abs(t["entry_price"] - t["stop_reference"]) or 1e-12
        ur = sign * (price - t["entry_price"]) / r_unit
        with session_scope() as db:
            dp = db.scalar(select(DemoPosition).where(DemoPosition.trade_id == t["id"]))
            if dp:
                dp.mark_price = pos.get("mark_price") or price
                dp.unrealized_pnl = pos.get("unrealized_pnl")
                dp.unrealized_r = round(ur, 3)
                dp.quantity = pos["quantity"]
                if t["direction"] == "LONG":
                    dp.best_price = max(dp.best_price or price, price)
                    dp.worst_price = min(dp.worst_price or price, price)
                else:
                    dp.best_price = min(dp.best_price or price, price)
                    dp.worst_price = max(dp.worst_price or price, price)
        res = adapter.ensure_stop(t["id"])
        if adapter.ensure_target(t["id"]) == "recreated":
            out["target_recreated"] += 1
        if res == "recreated":
            out["stop_recreated"] += 1
        elif res == "failed":
            botlog.critical(MODULE, f"{t['symbol']}: SL ausente e não recriado — fechando por segurança")
            adapter.close_trade(t["id"], "STOP", "failsafe: stop ausente e não recriável")
            continue
        else:
            with session_scope() as db:
                dp = db.scalar(select(DemoPosition).where(DemoPosition.trade_id == t["id"]))
                if dp:
                    dp.sl_verified_at = utcnow()
        if be_r and be_r > 0 and not t["breakeven_set"] and ur >= be_r:
            be = t["entry_price"] * (1 + sign * 2 * FEE_PCT / 100)
            if adapter.move_stop(t["id"], be, "breakeven"):
                out["breakeven"] += 1
    out["pnl_confirmed_late"] = confirm_pending()
    out["reclassified"] = reclassify_external()
    out["reconcile"] = reconcile(adapter, positions)
    return out


# ---------------------------------------------------------------- conciliação diário × corretora
broker_state: dict = {"positions": [], "orphans": [], "checked_at": None, "error": None}
_alerted: set = set()
EMERGENCY_STOP_PCT = 3.0


def reconcile(adapter, positions: list[dict] | None = None) -> dict:
    """Compara as posições da corretora com os trades OPEN do diário.
    - posição de um trade marcado erroneamente como fechado (EXTERNAL_CLOSE, mesma quantidade, ≤7 dias): o registro é
      REABERTO (com nota); se o preço já passou do stop/alvo, a posição é fechada com o motivo correspondente;
      senão o monitor recoloca stop e alvo no próximo ciclo;
    - posição sem trade correspondente: alerta no painel e stop de emergência se não houver stop na corretora."""
    client = adapter.client
    if positions is None:
        positions = client.get_positions()
        if not positions:
            time.sleep(2)
            positions = client.get_positions()
    busy = in_flight_symbols()
    open_keys = {(t["symbol"], t["direction"]) for t in open_trades()}
    try:
        orders = client.get_open_orders()
    except BingXError:
        orders = []
    stops = {o.get("symbol") for o in orders if str(o.get("type")) == "STOP_MARKET"}
    out = {"reopened": [], "closed": [], "foreign": []}
    orphans = []
    for p in positions:
        key = (p["symbol"], p["direction"])
        if key in open_keys or p["symbol"] in busy:
            continue
        tid = _reopen_if_ours(p)
        if tid:
            out["reopened"].append(p["symbol"])
            closed = _close_if_level_passed(adapter, tid)
            if closed:
                out["closed"].append(f"{p['symbol']} ({closed})")
            continue
        protected = p["symbol"] in stops
        if not protected:
            protected = _emergency_stop(adapter, p)
        orphans.append({**p, "protected": protected})
        out["foreign"].append(p["symbol"])
        if key not in _alerted:
            _alerted.add(key)
            events.emit("ERROR", symbol=p["symbol"], message=(
                f"posição {p['direction']} na corretora sem trade correspondente no diário "
                f"({'stop de emergência colocado' if protected else 'SEM STOP'})"))
    broker_state.update(positions=positions, orphans=orphans, checked_at=utcnow().isoformat(), error=None)
    return out


def _reopen_if_ours(p: dict) -> int | None:
    since = utcnow() - timedelta(days=7)
    with session_scope() as db:
        rows = db.scalars(select(TradeJournal).where(
            TradeJournal.symbol == p["symbol"], TradeJournal.direction == p["direction"],
            TradeJournal.status == "CLOSED", TradeJournal.exit_reason == "EXTERNAL_CLOSE",
            TradeJournal.exit_time >= since).order_by(TradeJournal.id.desc())).all()
        row = next((r for r in rows if abs((r.quantity or 0) - p["quantity"]) <= 1e-9 * max(1.0, p["quantity"])), None)
        if row is None:
            return None
        wrong = f"{row.exit_time:%d/%m %H:%M} UTC"
        row.status = "OPEN"
        for k in ("exit_time", "exit_price", "exit_reason", "exit_detail", "gross_pnl", "fees", "funding", "pnl",
                  "pnl_r", "mfe_r", "mae_r", "duration_minutes", "close_order_id"):
            setattr(row, k, None)
        row.pnl_confirmed = False
        row.notes = list(row.notes or []) + [{"at": utcnow().isoformat(), "text":
                     f"reaberto: tinha sido marcado como fechado em {wrong} por falha de leitura da corretora, "
                     "mas a posição continuava aberta; stop e alvo recolocados"}]
        dp = db.scalar(select(DemoPosition).where(DemoPosition.trade_id == row.id))
        if dp:
            dp.open = True
        else:
            db.add(DemoPosition(trade_id=row.id, symbol=row.symbol, direction=row.direction, quantity=row.quantity,
                                entry_price=row.entry_price))
        tid = row.id
    _misses.pop(tid, None)
    events.emit("TRADE_UPDATED", symbol=p["symbol"], trade_id=tid,
                message="trade reaberto: a posição continuava aberta na corretora (falha de leitura corrigida)")
    return tid


def _close_if_level_passed(adapter, tid: int) -> str | None:
    with session_scope() as db:
        t = db.get(TradeJournal, tid)
        sym, d, stop, tgt = t.symbol, t.direction, t.current_stop or t.stop_reference, t.target_reference
    try:
        px = data.market().get_price(sym)
    except BingXError:
        return None
    s = 1 if d == "LONG" else -1
    if s * (px - stop) <= 0:
        adapter.close_trade(tid, "STOP", f"stop ({stop}) ultrapassado enquanto a posição estava sem proteção; "
                                         f"fechada a mercado ({px})")
        return "stop ultrapassado"
    if s * (px - tgt) >= 0:
        adapter.close_trade(tid, "TARGET", f"alvo ({tgt}) atingido enquanto a posição estava sem proteção; "
                                           f"fechada a mercado ({px})")
        return "alvo atingido"
    return None


def _emergency_stop(adapter, p: dict) -> bool:
    try:
        px = data.market().get_price(p["symbol"])
        s = 1 if p["direction"] == "LONG" else -1
        stop = adapter.client.round_price(p["symbol"], px * (1 - s * EMERGENCY_STOP_PCT / 100))
        _, exit_side, position_side, extra = adapter._sides(p["direction"])
        adapter.client.place_order(symbol=p["symbol"], side=exit_side, positionSide=position_side, type="STOP_MARKET",
                                   quantity=p["quantity"], stopPrice=stop, workingType="MARK_PRICE", **extra)
        return True
    except BingXError as exc:
        botlog.critical(MODULE, f"{p['symbol']}: não foi possível colocar stop de emergência: {exc}")
        return False


def _position_of(client, t: dict) -> dict | None:
    try:
        return next((p for p in client.get_positions(t["symbol"]) if p["direction"] == t["direction"]), None)
    except BingXError:
        return {"symbol": t["symbol"], "direction": t["direction"], "quantity": t["quantity"], "unknown": True}


def _close_evidence(client, t: dict) -> bool:
    """A corretora confirma que a posição fechou? (ordem de saída executada ou PnL realizado após a entrada)."""
    with session_scope() as db:
        row = db.get(TradeJournal, t["id"])
        ids = [row.sl_order_id, row.tp_order_id, row.close_order_id]
    if any(_order_filled(client, t["symbol"], oid, tries=1) for oid in ids if oid):
        return True
    try:
        rows = client.get_income(t["symbol"], _ms(t["entry_time"]) + 1_000, None)
    except BingXError:
        return False
    return any(r.get("incomeType") == "REALIZED_PNL" for r in rows)


def _confirm_closed(adapter, t: dict) -> bool:
    n = _misses.get(t["id"], 0) + 1
    _misses[t["id"]] = n
    if n < MISS_CONFIRM:
        return False
    if _close_evidence(adapter.client, t) or n >= MISS_NO_EVIDENCE:
        _misses.pop(t["id"], None)
        return True
    return False


def _order_filled(client, symbol: str, oid: str | None, tries: int = 3) -> float | None:
    """Preço médio se a ordem foi executada. Tenta de novo: logo após o disparo a corretora pode ainda não ter
    atualizado o status (ou a consulta falhar)."""
    if not oid:
        return None
    for k in range(tries):
        try:
            o = client.get_order(symbol, oid)
            st = str(o.get("status")).upper()
            if st == "FILLED":
                return float(o.get("avgPrice") or 0) or None
            if st in ("CANCELLED", "CANCELED", "EXPIRED"):
                return None
        except BingXError:
            pass
        if k < tries - 1:
            time.sleep(1.5)
    return None


def _window_start(t: dict) -> int:
    """Início da janela de lançamentos do trade: a taxa de entrada pode ser cobrada até ~1 min antes do horário
    gravado (espera do preenchimento + stop/alvo + relógio do PC). Nunca invade o trade anterior do mesmo par."""
    start = _ms(t["entry_time"]) - 90_000
    with session_scope() as db:
        prev = db.scalar(select(TradeJournal.exit_time).where(
            TradeJournal.symbol == t["symbol"], TradeJournal.id != t["id"], TradeJournal.exit_time.is_not(None),
            TradeJournal.exit_time <= t["entry_time"]).order_by(TradeJournal.exit_time.desc()).limit(1))
    if prev is not None:
        start = max(start, _ms(prev) + 1_000)
    return start


def _pnl(client, t: dict, closed_at: datetime) -> dict | None:
    start = _window_start(t)
    end = _ms(closed_at) + 120_000
    try:
        rows = client.get_income(t["symbol"], start, end)
    except BingXError:
        return None
    if not any(r.get("incomeType") == "REALIZED_PNL" for r in rows):
        return None
    s = lambda kind: sum(float(r["income"]) for r in rows if r.get("incomeType") == kind)  # noqa: E731
    gross, fees, funding = s("REALIZED_PNL"), s("TRADING_FEE"), s("FUNDING_FEE")
    return {"gross_pnl": round(gross, 6), "fees": round(fees, 6), "funding": round(funding, 6),
            "pnl": round(gross + fees + funding, 6)}


def _excursions(t: dict, closed_at: datetime) -> tuple[float | None, float | None]:
    """MFE/MAE em R com candles de 5m da conta real entre a entrada e a saída."""
    try:
        start, end = _ms(t["entry_time"]), _ms(closed_at)
        kl = data.market().get_klines(t["symbol"], "5m", 1000, start_ms=start - 300_000, end_ms=end)
    except Exception:
        return None, None
    kl = [k for k in kl if k["time"] + 300_000 > start and k["time"] <= end]
    if not kl:
        return None, None
    r_unit = abs(t["entry_price"] - t["stop_reference"]) or 1e-12
    hi, lo = max(k["high"] for k in kl), min(k["low"] for k in kl)
    if t["direction"] == "LONG":
        return round((hi - t["entry_price"]) / r_unit, 3), round((lo - t["entry_price"]) / r_unit, 3)
    return round((t["entry_price"] - lo) / r_unit, 3), round((t["entry_price"] - hi) / r_unit, 3)


def _finalize(adapter, t: dict) -> bool:
    client = adapter.client
    closed_at = utcnow()
    with session_scope() as db:   # estado ATUAL (o motor pode ter mandado fechar depois do início do ciclo)
        row = db.get(TradeJournal, t["id"])
        t = {**t, **{k: getattr(row, k) for k in ("exit_reason", "exit_detail", "close_order_id", "sl_order_id",
                                                  "tp_order_id", "breakeven_set", "current_stop")}}
    reason, detail = t.get("exit_reason"), t.get("exit_detail")
    exit_price = None
    sl_px = _order_filled(client, t["symbol"], t["sl_order_id"])
    tp_px = _order_filled(client, t["symbol"], t["tp_order_id"])
    if t.get("close_order_id"):
        exit_price = _order_filled(client, t["symbol"], t["close_order_id"])
    if sl_px:
        exit_price = sl_px
        reason = "STOP"
        detail = "stop no breakeven (+taxas) acionado" if t["breakeven_set"] else "stop da estratégia acionado"
    elif tp_px:
        exit_price, reason, detail = tp_px, "TARGET", "alvo da estratégia primária atingido"
    elif not reason:
        reason, detail = "EXTERNAL_CLOSE", "posição fechada fora do motor (manual na corretora ou liquidação)"
    adapter.cancel_protection(t["symbol"], [t["sl_order_id"], t["tp_order_id"]])
    pnl = _pnl(client, t, closed_at)
    mfe, mae = _excursions(t, closed_at)
    if exit_price is None:
        try:
            exit_price = data.market().get_price(t["symbol"])
        except BingXError:
            exit_price = None
    with session_scope() as db:
        row = db.get(TradeJournal, t["id"])
        if row.status != "OPEN":
            return False
        row.status = "CLOSED"
        row.exit_time = closed_at
        row.exit_price = exit_price
        row.exit_reason = reason
        row.exit_detail = detail
        row.mfe_r, row.mae_r = mfe, mae
        row.duration_minutes = round((closed_at - _aware(row.entry_time)).total_seconds() / 60, 1)
        if pnl:
            for k, v in pnl.items():
                setattr(row, k, v)
            row.pnl_r = round(pnl["pnl"] / row.risk_usdt, 3) if row.risk_usdt else None
            row.pnl_confirmed = True
        elif exit_price and row.entry_price:
            # estimativa até a corretora publicar o PnL realizado (substituída por confirm_pending)
            sign = 1 if row.direction == "LONG" else -1
            gross = sign * (exit_price - row.entry_price) * row.quantity
            fees = -(row.entry_price + exit_price) * row.quantity * FEE_PCT / 100
            row.gross_pnl, row.fees, row.pnl = round(gross, 6), round(fees, 6), round(gross + fees, 6)
            row.pnl_r = round(row.pnl / row.risk_usdt, 3) if row.risk_usdt else None
            row.pnl_confirmed = False
        dp = db.scalar(select(DemoPosition).where(DemoPosition.trade_id == row.id))
        if dp:
            dp.open = False
        msg = (f"{row.direction} fechado por {reason}: PnL {row.pnl:+.4f} ({row.pnl_r:+.2f}R)" if pnl
               else f"{row.direction} fechado por {reason}: PnL real ainda não disponível (confirmação pendente)")
        tid, sym, strat = row.id, row.symbol, row.strategy_primary
    events.emit("TRADE_CLOSED", symbol=sym, strategy=strat, trade_id=tid, exit_reason=reason, detail=detail,
                pnl=pnl and pnl["pnl"], mfe_r=mfe, mae_r=mae, message=msg)
    return True


def reclassify_external(max_age_hours: int = 72) -> int:
    """Revê trades marcados como EXTERNAL_CLOSE: se o stop ou o alvo constam como executados na corretora, corrige
    o motivo e o preço de saída (com nota no diário explicando a correção)."""
    since = utcnow() - timedelta(hours=max_age_hours)
    with session_scope() as db:
        rows = db.scalars(select(TradeJournal).where(TradeJournal.exit_reason == "EXTERNAL_CLOSE",
                                                     TradeJournal.exit_time >= since)).all()
        pend = [{c.name: getattr(r, c.name) for c in TradeJournal.__table__.columns} for r in rows]
    if not pend:
        return 0
    client = data.execution()
    n = 0
    for t in pend:
        sl = _order_filled(client, t["symbol"], t["sl_order_id"], tries=1)
        tp = None if sl else _order_filled(client, t["symbol"], t["tp_order_id"], tries=1)
        if not (sl or tp):
            continue
        with session_scope() as db:
            row = db.get(TradeJournal, t["id"])
            row.exit_reason = "STOP" if sl else "TARGET"
            row.exit_detail = ("stop no breakeven (+taxas) acionado" if sl and row.breakeven_set
                               else "stop da estratégia acionado" if sl else "alvo da estratégia primária atingido")
            row.exit_price = sl or tp
            row.notes = list(row.notes or []) + [{"at": utcnow().isoformat(), "text":
                         f"motivo de saída corrigido de EXTERNAL_CLOSE para {row.exit_reason} (ordem executada na corretora)"}]
        events.emit("TRADE_UPDATED", symbol=t["symbol"], trade_id=t["id"],
                    message=f"motivo de saída corrigido: EXTERNAL_CLOSE → {'STOP' if sl else 'TARGET'}")
        n += 1
    return n


def confirm_pending(max_age_hours: int = 72) -> int:
    with session_scope() as db:
        rows = db.scalars(select(TradeJournal).where(TradeJournal.status == "CLOSED",
                                                     TradeJournal.pnl_confirmed.is_(False))).all()
        pend = [{c.name: getattr(r, c.name) for c in TradeJournal.__table__.columns} for r in rows]
    if not pend:
        return 0
    client = data.execution()
    n = 0
    for t in pend:
        if t["exit_time"] is None or (utcnow() - _aware(t["exit_time"])).total_seconds() > max_age_hours * 3600:
            continue
        pnl = _pnl(client, t, _aware(t["exit_time"]))
        if not pnl:
            continue
        with session_scope() as db:
            row = db.get(TradeJournal, t["id"])
            for k, v in pnl.items():
                setattr(row, k, v)
            row.pnl_r = round(pnl["pnl"] / row.risk_usdt, 3) if row.risk_usdt else None
            row.pnl_confirmed = True
        n += 1
    return n
