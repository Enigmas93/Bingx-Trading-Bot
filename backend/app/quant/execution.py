"""Adaptadores de execução.

DemoExecutionAdapter — envia ordens SOMENTE para a conta DEMO (VST). Antes de qualquer ação chama
safety.assert_demo(); o BingXClient tem uma segunda trava (_assert_execution_allowed). O preço de referência e os
níveis vêm da análise feita com dados da conta REAL (quant/data.py); a demo só recebe as ordens.

LiveExecutionAdapter — existe apenas para deixar explícito o ponto de troca futuro. Nesta versão ele SEMPRE recusa
(LIVE_EXECUTION_BLOCKED). Não há caminho de interface/API que o selecione.

O módulo de pesquisa (research.py) não importa este arquivo.
"""

import time
import uuid

from .. import botlog
from ..bingx import BingXError
from ..common import executing, get_config
from ..database import session_scope
from ..executor import _confirm_order, _wait_fill
from ..models import utcnow
from . import data, events
from .models import DemoPosition, TradeFeatures, TradeJournal
from .safety import LiveExecutionBlocked, assert_demo

MODULE = "quant.exec"


class ExecutionFailed(RuntimeError):
    pass


class LiveExecutionAdapter:
    mode = "LIVE"

    def __getattr__(self, name):  # qualquer método → bloqueado
        def blocked(*a, **k):
            events.emit("LIVE_CONFIGURATION_DETECTED", message=f"tentativa de usar LiveExecutionAdapter.{name}")
            raise LiveExecutionBlocked("LIVE EXECUTION: BLOCKED nesta versão (somente DEMO)")
        return blocked


class DemoExecutionAdapter:
    mode = "DEMO"

    def __init__(self):
        assert_demo()
        self.client = data.execution()

    # ------------------------------------------------------------------ helpers
    def _sides(self, direction: str):
        hedge = self.client.is_hedge_mode()
        position_side = direction if hedge else "BOTH"
        extra = {} if hedge else {"reduceOnly": "true"}
        entry_side, exit_side = ("BUY", "SELL") if direction == "LONG" else ("SELL", "BUY")
        return entry_side, exit_side, position_side, extra

    def _size(self, symbol: str, price: float, stop: float) -> tuple[float, float, int]:
        cfg = get_config()
        available = self.client.get_balance()["available"]
        if available <= 0:
            raise ExecutionFailed(f"saldo disponível DEMO insuficiente ({available})")
        lev = cfg.leverage
        max_notional = available * cfg.capital_percent / 100 * lev
        dist = abs(price - stop)
        risk_budget = available * cfg.risk_per_trade_pct / 100
        notional = min(risk_budget / dist * price, max_notional) if dist > 0 else max_notional
        qty = self.client.round_qty(symbol, notional / price)
        mn = self.client.min_qty(symbol, price)
        if qty <= 0 or qty < mn:
            raise ExecutionFailed(f"quantidade {qty} abaixo do mínimo {mn} (notional {notional:.2f}, {lev}x)")
        return qty, round(dist * qty, 6), lev

    # ------------------------------------------------------------------ abrir
    def open_trade(self, opp: dict, journal_fields: dict, snapshot: dict) -> dict:
        """Abre a posição DEMO. `opp` traz symbol/direction/ref_price/stop/target da estratégia primária
        (calculados com dados da conta real). Retorna {success, trade_id, ...}."""
        assert_demo()
        symbol, direction = opp["symbol"], opp["direction"]
        entry_side, exit_side, position_side, extra = self._sides(direction)
        live_price = data.market().get_price(symbol)          # referência: conta real
        stop_pct = (opp["stop"] - opp["ref_price"]) / opp["ref_price"]
        target_pct = (opp["target"] - opp["ref_price"]) / opp["ref_price"]
        notes = []

        with executing(symbol):
            try:
                try:
                    self.client.set_margin_type(symbol, "CROSSED")
                except BingXError as exc:
                    botlog.info(MODULE, f"{symbol}: marginType -> {exc.msg}")
                qty, _, lev = self._size(symbol, live_price, live_price * (1 + stop_pct))
                self.client.set_leverage(symbol, position_side, lev)
                order = self.client.place_order(symbol=symbol, side=entry_side, positionSide=position_side,
                                                type="MARKET", quantity=qty)
            except (BingXError, ExecutionFailed) as exc:
                return {"success": False, "error": f"entrada não enviada: {exc}"}
            entry_order_id = str(order.get("orderId"))
            fill = _wait_fill(self.client, symbol, entry_order_id)
            if fill is None:
                pos = next((p for p in self._positions(symbol) if p["direction"] == direction), None)
                if pos is None:
                    return {"success": False, "error": "entrada não confirmada e posição não encontrada na DEMO"}
                fill = (pos["entry_price"], pos["quantity"])
                notes.append("preenchimento lido da posição (ordem não confirmou a tempo)")
            fill_price, fill_qty = fill

            # níveis: mesma distância percentual da análise, aplicada ao preço executado
            stop = self.client.round_price(symbol, fill_price * (1 + stop_pct))
            target = self.client.round_price(symbol, fill_price * (1 + target_pct))
            valid = (stop < fill_price < target) if direction == "LONG" else (target < fill_price < stop)
            sl_id = tp_id = None
            if valid:
                try:
                    sl = self.client.place_order(symbol=symbol, side=exit_side, positionSide=position_side,
                                                 type="STOP_MARKET", quantity=fill_qty, stopPrice=stop,
                                                 workingType="MARK_PRICE", **extra)
                    sl_id = str(sl.get("orderId"))
                    if not _confirm_order(self.client, symbol, sl_id, 5.0):
                        notes.append("SL enviado, confirmação atrasada (o monitor verifica a cada ciclo)")
                except BingXError as exc:
                    notes.append(f"falha ao criar SL: {exc}")
            else:
                notes.append(f"níveis inválidos após execução (stop {stop}, preço {fill_price}, alvo {target})")

            if sl_id is None:
                # sem stop = posição sem proteção: fecha imediatamente (failsafe)
                closed = self._market_close(symbol, direction, fill_qty)
                botlog.critical(MODULE, f"{symbol}: sem stop → posição DEMO fechada pelo failsafe ({closed})")
                tid = self._persist(opp, journal_fields, snapshot, status="FAILED", fill_price=fill_price,
                                    qty=fill_qty, lev=lev, stop=stop, target=target, live_price=live_price,
                                    ids={"entry_order_id": entry_order_id}, notes=notes + ["failsafe: fechada"])
                return {"success": False, "error": "; ".join(notes), "trade_id": tid}

            try:
                tp = self.client.place_order(symbol=symbol, side=exit_side, positionSide=position_side,
                                             type="TAKE_PROFIT_MARKET", quantity=fill_qty, stopPrice=target,
                                             workingType="MARK_PRICE", **extra)
                tp_id = str(tp.get("orderId"))
            except BingXError as exc:
                notes.append(f"falha ao criar alvo (o motor de saída fecha no alvo): {exc}")

            tid = self._persist(opp, journal_fields, snapshot, status="OPEN", fill_price=fill_price, qty=fill_qty,
                                lev=lev, stop=stop, target=target, live_price=live_price,
                                ids={"entry_order_id": entry_order_id, "sl_order_id": sl_id, "tp_order_id": tp_id},
                                notes=notes)
        return {"success": True, "trade_id": tid, "entry_price": fill_price, "quantity": fill_qty,
                "stop": stop, "target": target, "warnings": notes}

    def _positions(self, symbol: str) -> list[dict]:
        try:
            return self.client.get_positions(symbol)
        except BingXError:
            return []

    def _persist(self, opp, jf, snapshot, status, fill_price, qty, lev, stop, target, live_price, ids, notes) -> int:
        now = utcnow()
        risk = abs(fill_price - stop) * qty
        with session_scope() as db:
            t = TradeJournal(
                trade_uid=f"T-{now:%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}", status=status, execution_mode="DEMO",
                symbol=opp["symbol"], direction=opp["direction"], timeframe=opp.get("timeframe", "1h"),
                entry_time=now, expected_entry_price=live_price, entry_price=fill_price, quantity=qty,
                risk_usdt=round(risk, 6), leverage=lev, stop_reference=stop, target_reference=target,
                current_stop=stop, slippage_pct=round((fill_price / live_price - 1) * 100, 5),
                notes=[{"at": now.isoformat(), "text": n} for n in notes], **ids, **jf)
            if status == "FAILED":
                t.exit_time, t.exit_reason, t.exit_detail = now, "FAILSAFE", "; ".join(notes)
            db.add(t)
            db.flush()
            db.add(TradeFeatures(trade_id=t.id, features=snapshot.get("features", {}),
                                 signals=snapshot.get("signals", []), regime=snapshot.get("regime", {}),
                                 opportunity=snapshot.get("opportunity", {})))
            if status == "OPEN":
                db.add(DemoPosition(trade_id=t.id, symbol=t.symbol, direction=t.direction, quantity=qty,
                                    entry_price=fill_price, mark_price=fill_price, best_price=fill_price,
                                    worst_price=fill_price, sl_verified_at=now))
            tid, uid = t.id, t.trade_uid
        events.emit("TRADE_OPENED" if status == "OPEN" else "ERROR", symbol=opp["symbol"],
                    strategy=jf.get("strategy_primary"), trade_id=tid, trade_uid=uid, direction=opp["direction"],
                    entry=fill_price, expected_entry_live=live_price, stop=stop, target=target, qty=qty,
                    confluence=jf.get("confluence_level"), final_score=jf.get("final_score"),
                    message=(f"{opp['direction']} {qty} @ {fill_price} (ref. real {live_price}) stop {stop} alvo {target}"
                             if status == "OPEN" else "abertura falhou: " + "; ".join(notes)))
        return tid

    # ------------------------------------------------------------------ fechar
    def _market_close(self, symbol: str, direction: str, qty: float) -> str:
        assert_demo()
        _, exit_side, position_side, extra = self._sides(direction)
        try:
            o = self.client.place_order(symbol=symbol, side=exit_side, positionSide=position_side, type="MARKET",
                                        quantity=qty, **extra)
            return str(o.get("orderId"))
        except BingXError as exc:
            botlog.critical(MODULE, f"{symbol}: falha ao fechar a mercado: {exc}")
            return ""

    def cancel_protection(self, symbol: str, order_ids: list[str | None]) -> None:
        for oid in order_ids:
            if not oid:
                continue
            try:
                self.client.cancel_order(symbol, oid)
            except BingXError:
                pass

    def close_trade(self, trade_id: int, reason: str, detail: str) -> dict:
        """Fecha a posição DEMO do trade (saída do motor ou manual). O monitor confirma e grava o PnL real."""
        assert_demo()
        with session_scope() as db:
            t = db.get(TradeJournal, trade_id)
            if t is None or t.status != "OPEN":
                return {"success": False, "error": "trade não está aberto"}
            symbol, direction, ids = t.symbol, t.direction, [t.sl_order_id, t.tp_order_id]
        pos = next((p for p in self._positions(symbol) if p["direction"] == direction), None)
        if pos is None:
            return {"success": False, "error": "posição não encontrada na DEMO (o monitor vai reconciliar)"}
        # O motivo é gravado ANTES da ordem e o par fica marcado como "em execução": o monitor (outra thread)
        # não finaliza o trade no meio do fechamento nem o registra como fechamento externo.
        with executing(symbol):
            with session_scope() as db:
                t = db.get(TradeJournal, trade_id)
                t.exit_reason, t.exit_detail = reason, detail
            oid = self._market_close(symbol, direction, pos["quantity"])
            if not oid:
                with session_scope() as db:
                    t = db.get(TradeJournal, trade_id)
                    t.exit_reason = t.exit_detail = None
                return {"success": False, "error": "ordem de fechamento rejeitada"}
            with session_scope() as db:
                db.get(TradeJournal, trade_id).close_order_id = oid
            time.sleep(1.0)
            self.cancel_protection(symbol, ids)
        events.emit("TRADE_UPDATED", symbol=symbol, trade_id=trade_id, message=f"fechamento enviado: {reason} — {detail}")
        return {"success": True, "close_order_id": oid}

    def move_stop(self, trade_id: int, new_stop: float, why: str) -> bool:
        """Substitui o SL (ex.: breakeven). Cria o novo antes de cancelar o antigo."""
        assert_demo()
        with session_scope() as db:
            t = db.get(TradeJournal, trade_id)
            symbol, direction, qty, old = t.symbol, t.direction, t.quantity, t.sl_order_id
        _, exit_side, position_side, extra = self._sides(direction)
        new_stop = self.client.round_price(symbol, new_stop)
        try:
            o = self.client.place_order(symbol=symbol, side=exit_side, positionSide=position_side, type="STOP_MARKET",
                                        quantity=qty, stopPrice=new_stop, workingType="MARK_PRICE", **extra)
        except BingXError as exc:
            botlog.warn(MODULE, f"{symbol}: não foi possível mover o stop ({why}): {exc}")
            return False
        self.cancel_protection(symbol, [old])
        with session_scope() as db:
            t = db.get(TradeJournal, trade_id)
            t.sl_order_id = str(o.get("orderId"))
            t.current_stop = new_stop
            if why == "breakeven":
                t.breakeven_set = True
            t.notes = list(t.notes or []) + [{"at": utcnow().isoformat(), "text": f"stop movido para {new_stop} ({why})"}]
        events.emit("TRADE_UPDATED", symbol=symbol, trade_id=trade_id, message=f"stop → {new_stop} ({why})")
        return True

    def ensure_stop(self, trade_id: int) -> str:
        """Garante que existe um SL ativo para o trade; recria se sumiu. Retorna 'ok' | 'recreated' | 'failed'."""
        assert_demo()
        with session_scope() as db:
            t = db.get(TradeJournal, trade_id)
            symbol, sl_id, stop = t.symbol, t.sl_order_id, t.current_stop or t.stop_reference
        try:
            open_ids = {str(o.get("orderId")) for o in self.client.get_open_orders(symbol)}
        except BingXError:
            return "ok"  # sem como verificar agora; tenta no próximo ciclo
        if sl_id and sl_id in open_ids:
            return "ok"
        ok = self.move_stop(trade_id, stop, "SL ausente na corretora: recriado")
        return "recreated" if ok else "failed"

    def ensure_target(self, trade_id: int) -> str:
        """Garante a ordem de alvo na corretora; recria se sumiu. Retorna 'ok' | 'recreated' | 'failed'."""
        assert_demo()
        with session_scope() as db:
            t = db.get(TradeJournal, trade_id)
            symbol, direction, qty, tp_id, target = t.symbol, t.direction, t.quantity, t.tp_order_id, t.target_reference
        try:
            open_ids = {str(o.get("orderId")) for o in self.client.get_open_orders(symbol)}
        except BingXError:
            return "ok"
        if tp_id and tp_id in open_ids:
            return "ok"
        _, exit_side, position_side, extra = self._sides(direction)
        try:
            o = self.client.place_order(symbol=symbol, side=exit_side, positionSide=position_side,
                                        type="TAKE_PROFIT_MARKET", quantity=qty,
                                        stopPrice=self.client.round_price(symbol, target), workingType="MARK_PRICE",
                                        **extra)
        except BingXError as exc:
            botlog.warn(MODULE, f"{symbol}: não foi possível recriar o alvo: {exc}")
            return "failed"
        with session_scope() as db:
            t = db.get(TradeJournal, trade_id)
            t.tp_order_id = str(o.get("orderId"))
            t.notes = list(t.notes or []) + [{"at": utcnow().isoformat(), "text": "alvo ausente na corretora: recriado"}]
        events.emit("TRADE_UPDATED", symbol=symbol, trade_id=trade_id, message="alvo ausente na corretora: recriado")
        return "recreated"


def get_adapter() -> DemoExecutionAdapter:
    """Único ponto de obtenção do adaptador. Nesta versão é sempre DEMO (LIVE nunca é retornado)."""
    return DemoExecutionAdapter()
