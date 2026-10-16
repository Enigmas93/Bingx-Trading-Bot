"""executeManualSignal: abre a posição na BingX com SL + TP1/TP2/TP3."""

import threading
import time

from . import botlog
from .bingx import BingXClient, BingXError, get_client
from .common import executing, get_config
from .database import session_scope
from .models import Position, Signal, Trade, utcnow

MODULE = "executor"
TP1_FRACTION = 0.5  # TP1 = 50% da posição (ou 100% se a qty for muito pequena)
TP2_WEIGHT, TP3_WEIGHT = 40, 30  # restante dividido na proporção 40:30
FILL_TRIES, FILL_INTERVAL = 10, 0.5
SL_CONFIRM_TIMEOUT = 5.0
DEFERRED_TP_DELAY = 2.0


class ExecutionError(Exception):
    pass


def _wait_fill(client: BingXClient, symbol: str, order_id: str) -> tuple[float, float] | None:
    """Aguarda confirmação da ordem MARKET: até 10 tentativas de 500ms."""
    for _ in range(FILL_TRIES):
        time.sleep(FILL_INTERVAL)
        try:
            order = client.get_order(symbol, order_id)
        except BingXError:
            continue
        price = float(order.get("avgPrice") or 0)
        qty = float(order.get("executedQty") or 0)
        if str(order.get("status")).upper() == "FILLED" and price > 0 and qty > 0:
            return price, qty
    return None


def _confirm_order(client: BingXClient, symbol: str, order_id: str, timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            order = client.get_order(symbol, order_id)
            if str(order.get("status")).upper() in ("NEW", "PENDING", "PARTIALLY_FILLED", "FILLED", "TRIGGERED"):
                return True
        except BingXError:
            pass
        time.sleep(0.5)
    return False


def recalc_levels(sig: Signal, current: float) -> dict:
    """Converte SL/TP teóricos em percentuais e reaplica sobre o preço ATUAL real."""
    base = sig.entry_price
    return {name: current * (1 + (getattr(sig, name) - base) / base)
            for name in ("stop_loss", "tp1", "tp2", "tp3")}


def validate_levels(direction: str, current: float, lv: dict) -> str | None:
    if direction == "LONG":
        ok = lv["stop_loss"] < current < lv["tp1"] < lv["tp2"] < lv["tp3"]
        rule = "SL < atual < TP1 < TP2 < TP3"
    else:
        ok = lv["tp3"] < lv["tp2"] < lv["tp1"] < current < lv["stop_loss"]
        rule = "TP3 < TP2 < TP1 < atual < SL"
    if ok and lv["stop_loss"] > 0 and lv["tp3"] > 0:
        return None
    return f"Validação direcional falhou ({rule}): atual={current} níveis={lv}"


def _persist(sig: Signal, qty: float, entry_price: float, leverage: int, levels: dict,
             order_ids: dict, protected: bool, errors: list[str]) -> tuple[int, int]:
    with session_scope() as db:
        trade = Trade(
            signal_id=sig.id, symbol=sig.symbol, direction=sig.direction, quantity=qty,
            entry_price=entry_price, leverage=leverage, margin_mode="CROSSED",
            stop_loss=levels["stop_loss"], tp1=levels["tp1"], tp2=levels["tp2"], tp3=levels["tp3"],
            errors="\n".join(errors) or None, **order_ids,
        )
        db.add(trade)
        db.flush()
        position = Position(
            trade_id=trade.id, symbol=sig.symbol, direction=sig.direction, quantity=qty,
            entry_price=entry_price, current_price=entry_price, leverage=leverage,
            stop_loss=levels["stop_loss"], tp1=levels["tp1"], tp2=levels["tp2"], tp3=levels["tp3"],
            protected=protected, source="bot", risk_amount=abs(entry_price - levels["stop_loss"]) * qty,
        )
        db.add(position)
        db.flush()
        return trade.id, position.id


def _place_deferred_tps(trade_id: int, symbol: str, exit_side: str, position_side: str, extra: dict,
                        remaining: float, levels: dict, min_qty: float) -> None:
    """TP2/TP3 criados em background (não bloqueiam a resposta de sucesso)."""
    client = get_client()
    tp2_qty = client.round_qty(symbol, remaining * TP2_WEIGHT / (TP2_WEIGHT + TP3_WEIGHT))
    tp3_qty = client.round_qty(symbol, remaining - tp2_qty)
    if tp2_qty < min_qty or tp3_qty < min_qty:  # não dá para dividir: tudo no TP2
        tp2_qty, tp3_qty = remaining, 0.0

    ids: dict[str, str] = {}
    for name, qty in (("tp2", tp2_qty), ("tp3", tp3_qty)):
        if qty <= 0:
            continue
        try:
            order = client.place_order(
                symbol=symbol, side=exit_side, positionSide=position_side, type="TAKE_PROFIT_MARKET",
                quantity=qty, stopPrice=levels[name], workingType="MARK_PRICE", **extra,
            )
            ids[f"{name}_order_id"] = str(order.get("orderId"))
            botlog.info(MODULE, f"{symbol}: {name.upper()} criado @ {levels[name]} qty={qty}")
        except Exception as exc:
            botlog.error(MODULE, f"{symbol}: falha ao criar {name.upper()} (deferred): {exc}")
    if ids:
        with session_scope() as db:
            trade = db.get(Trade, trade_id)
            for key, value in ids.items():
                setattr(trade, key, value)


def execute_signal(signal_id: int, leverage: int | None = None, capital_percent: float | None = None) -> dict:
    client = get_client()
    cfg = get_config()
    leverage = leverage or cfg.leverage
    capital_percent = capital_percent or cfg.capital_percent

    with session_scope() as db:
        sig = db.get(Signal, signal_id)
    if sig is None:
        return {"success": False, "error": f"Sinal {signal_id} não encontrado"}
    if sig.status != "approved":
        return {"success": False, "error": f"Sinal {signal_id} não está 'approved' (status={sig.status})"}

    symbol, direction = sig.symbol, sig.direction
    is_long = direction == "LONG"
    entry_side, exit_side = ("BUY", "SELL") if is_long else ("SELL", "BUY")

    # 1) Configuração inicial: margem CROSS, alavancagem, saldo
    try:
        hedge = client.is_hedge_mode()
        position_side = direction if hedge else "BOTH"
        extra = {} if hedge else {"reduceOnly": "true"}
        try:
            client.set_margin_type(symbol, "CROSSED")
        except BingXError as exc:  # normalmente "já está em CROSSED"
            botlog.info(MODULE, f"{symbol}: marginType -> {exc.msg}")
        client.set_leverage(symbol, position_side, leverage)

        available = client.get_balance()["available"]
        if available <= 0:
            raise ExecutionError(f"Saldo disponível insuficiente ({available} USDT)")

        # 2) Cálculo de quantidade
        capital_per_position = available * (capital_percent / 100)
        max_notional = capital_per_position * leverage
        stop_distance = abs(sig.entry_price - sig.stop_loss)
        if cfg.sizing_mode == "risk" and stop_distance > 0:
            # Arrisca risk_per_trade_pct do saldo; capital_percent vira o teto de margem por posição.
            risk_budget = available * (cfg.risk_per_trade_pct / 100)
            notional = min(risk_budget / stop_distance * sig.entry_price, max_notional)
        else:
            notional = max_notional
        qty = client.round_qty(symbol, notional / sig.entry_price)
        min_qty = client.min_qty(symbol, sig.entry_price)
        if qty <= 0 or qty < min_qty:
            raise ExecutionError(f"Quantidade {qty} abaixo do mínimo {min_qty:.8f} "
                                 f"(notional {notional:.2f} USDT, {leverage}x)")
    except (BingXError, ExecutionError) as exc:
        botlog.error(MODULE, f"{symbol}: falha na preparação: {exc}")
        return {"success": False, "error": str(exc)}

    # 3) Ordem de entrada MARKET
    try:
        order = client.place_order(symbol=symbol, side=entry_side, positionSide=position_side,
                                   type="MARKET", quantity=qty)
    except BingXError as exc:
        botlog.error(MODULE, f"{symbol}: ordem de entrada rejeitada: {exc}")
        return {"success": False, "error": f"Ordem de entrada rejeitada: {exc}"}
    entry_order_id = str(order.get("orderId"))
    errors: list[str] = []

    fill = _wait_fill(client, symbol, entry_order_id)
    if fill is None:
        # Fallback: lê a posição aberta na corretora
        try:
            pos = next((p for p in client.get_positions(symbol) if p["direction"] == direction), None)
        except BingXError:
            pos = None
        if pos:
            fill = (pos["entry_price"], pos["quantity"])
        else:
            fill = (sig.entry_price, qty)
            errors.append("Preenchimento da entrada não confirmado; usando valores teóricos")
            botlog.warn(MODULE, f"{symbol}: entrada não confirmada em {FILL_TRIES} tentativas")
    actual_entry_price, actual_qty = fill
    botlog.info(MODULE, f"{symbol}: {direction} aberto qty={actual_qty} @ {actual_entry_price} (ordem {entry_order_id})")

    # 4) Preço de mercado ATUAL
    try:
        current = client.get_price(symbol)
    except BingXError:
        current = actual_entry_price

    # 5) Recálculo dinâmico + validação direcional
    levels = {k: client.round_price(symbol, v) for k, v in recalc_levels(sig, current).items()}
    order_ids = {"entry_order_id": entry_order_id}
    invalid = validate_levels(direction, current, levels)
    if invalid:
        errors.append(invalid)
        botlog.critical(MODULE, f"{symbol}: {invalid}. POSIÇÃO DESPROTEGIDA (sem SL)!")
        trade_id, position_id = _persist(sig, actual_qty, actual_entry_price, leverage, levels,
                                         order_ids, protected=False, errors=errors)
        return {"success": False, "error": invalid, "position_unprotected": True,
                "trade_id": trade_id, "position_id": position_id}

    # 6a) Stop Loss (STOP_MARKET)
    try:
        sl_order = client.place_order(
            symbol=symbol, side=exit_side, positionSide=position_side, type="STOP_MARKET",
            quantity=actual_qty, stopPrice=levels["stop_loss"], workingType="MARK_PRICE", **extra,
        )
        order_ids["sl_order_id"] = str(sl_order.get("orderId"))
        if not _confirm_order(client, symbol, order_ids["sl_order_id"], SL_CONFIRM_TIMEOUT):
            botlog.warn(MODULE, f"{symbol}: SL enviado mas não confirmado em {SL_CONFIRM_TIMEOUT:.0f}s; seguindo")
    except BingXError as exc:
        # FAILSAFE: retorna erro, posição fica DESPROTEGIDA
        errors.append(f"Falha ao criar Stop Loss: {exc}")
        botlog.critical(MODULE, f"{symbol}: FALHA AO CRIAR STOP LOSS ({exc}). POSIÇÃO DESPROTEGIDA!")
        trade_id, position_id = _persist(sig, actual_qty, actual_entry_price, leverage, levels,
                                         order_ids, protected=False, errors=errors)
        return {"success": False, "error": f"Stop Loss falhou: {exc}", "position_unprotected": True,
                "trade_id": trade_id, "position_id": position_id}

    # 6b) TP1 (TAKE_PROFIT_MARKET): 50% ou 100% se qty muito pequena
    min_qty = client.min_qty(symbol, current)
    tp1_qty = client.round_qty(symbol, actual_qty * TP1_FRACTION)
    if tp1_qty < min_qty or actual_qty - tp1_qty < min_qty:
        tp1_qty = actual_qty
    try:
        tp1_order = client.place_order(
            symbol=symbol, side=exit_side, positionSide=position_side, type="TAKE_PROFIT_MARKET",
            quantity=tp1_qty, stopPrice=levels["tp1"], workingType="MARK_PRICE", **extra,
        )
        order_ids["tp1_order_id"] = str(tp1_order.get("orderId"))
    except BingXError as exc:
        errors.append(f"Falha ao criar TP1: {exc}")
        botlog.error(MODULE, f"{symbol}: falha ao criar TP1: {exc}")

    trade_id, position_id = _persist(sig, actual_qty, actual_entry_price, leverage, levels,
                                     order_ids, protected=True, errors=errors)

    # 6c) TP2/TP3 DEFERRED: em background após 2s
    remaining = client.round_qty(symbol, actual_qty - tp1_qty)
    if remaining > 0:
        threading.Timer(DEFERRED_TP_DELAY, _place_deferred_tps,
                        args=(trade_id, symbol, exit_side, position_side, extra, remaining, levels, min_qty)).start()

    botlog.info(MODULE, f"{symbol}: SL {levels['stop_loss']} | TP1 {levels['tp1']} ({tp1_qty}) | "
                        f"TP2 {levels['tp2']} / TP3 {levels['tp3']} agendados ({remaining})")
    return {"success": True, "trade_id": trade_id, "position_id": position_id,
            "entry_price": actual_entry_price, "quantity": actual_qty, "levels": levels, "warnings": errors}


def execute_and_mark(signal_id: int) -> dict:
    """Executa e marca o sinal como executed/failed."""
    with session_scope() as db:
        sig = db.get(Signal, signal_id)
        symbol = sig.symbol if sig else None
    if symbol:
        with executing(symbol):  # até a posição estar gravada, o sync não a trata como externa
            result = execute_signal(signal_id)
    else:
        result = execute_signal(signal_id)
    with session_scope() as db:
        sig = db.get(Signal, signal_id)
        if sig is not None and sig.status == "approved":
            if result.get("success"):
                sig.status = "executed"
                sig.executed_at = utcnow()
            else:
                sig.status = "failed"
                sig.status_reason = result.get("error")
    return result
