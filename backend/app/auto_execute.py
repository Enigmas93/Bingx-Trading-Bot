"""autoExecuteSignals: pega o melhor sinal aprovado, checa risco e notícias e executa."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import select

from . import botlog
from .bingx import get_client
from .common import expire_stale_signals, get_config
from .database import session_scope
from .executor import execute_and_mark
from .models import Position, Signal, utcnow
from .news import check_news_before_trade
from .risk import cooldown_symbols, risk_state

MODULE = "auto_execute"


def _cancel(signal_id: int, reason: str, news: dict | None = None) -> None:
    with session_scope() as db:
        sig = db.get(Signal, signal_id)
        sig.status = "cancelled"
        sig.status_reason = reason
        if news is not None:
            sig.news_result = news


TIMEFRAME_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200,
                     "4h": 14400, "6h": 21600, "12h": 43200, "1d": 86400}


def candle_breadth(timeframe: str) -> int:
    """Nº de pares distintos com sinal gerado desde o fechamento do último candle."""
    step = TIMEFRAME_SECONDS.get(timeframe, 3600)
    now = utcnow()
    candle_close = datetime.fromtimestamp(int(now.timestamp()) // step * step, tz=timezone.utc)
    with session_scope() as db:
        rows = db.scalars(select(Signal.symbol).where(Signal.created_at >= candle_close.replace(tzinfo=None))).all()
    return len(set(rows))


def run_auto_execute() -> dict:
    # Falhas individuais NUNCA desativam a automação: sempre retorna success=True.
    try:
        return _run()
    except Exception as exc:
        botlog.error(MODULE, f"Erro no ciclo de auto-execução: {exc}")
        return {"success": True, "error": str(exc)}


def _run() -> dict:
    cfg = get_config()
    if not cfg.automation_enabled:
        return {"success": True, "skipped": "Automação desligada"}

    client = get_client()

    # Posições abertas (corretora) e controles de risco
    if client.has_credentials:
        open_positions = client.get_positions()
        equity = client.get_balance()["equity"]
    else:
        with session_scope() as db:
            open_positions = [{"symbol": p.symbol, "direction": p.direction}
                              for p in db.scalars(select(Position).where(Position.status == "open"))]
        equity = None

    state = risk_state(cfg, equity)
    if state["paused"]:
        botlog.warn(MODULE, f"Novas entradas pausadas: {state['reason']}")
        return {"success": True, "skipped": state["reason"]}

    if len(open_positions) >= cfg.max_positions:
        botlog.info(MODULE, f"Limite de posições atingido ({len(open_positions)}/{cfg.max_positions})")
        return {"success": True, "skipped": "Limite de posições atingido"}

    expire_stale_signals(cfg)

    with session_scope() as db:
        signals = db.scalars(
            select(Signal).where(Signal.status == "approved").order_by(Signal.confidence_score.desc())
        ).all()
    if not signals:
        return {"success": True, "skipped": "Nenhum sinal aprovado"}

    # Amplitude: quantos pares deram sinal no candle atual (mercado em tendência ampla)
    breadth = candle_breadth(cfg.timeframe)
    if breadth < cfg.min_breadth:
        msg = f"Amplitude insuficiente: {breadth} par(es) com sinal neste candle (mínimo {cfg.min_breadth})"
        botlog.info(MODULE, msg)
        return {"success": True, "skipped": msg}

    busy = {p["symbol"] for p in open_positions}
    per_direction = {"LONG": 0, "SHORT": 0}
    for p in open_positions:
        per_direction[p["direction"]] = per_direction.get(p["direction"], 0) + 1
    cooling = cooldown_symbols(cfg)

    # Filtra candidatos elegíveis (melhor score primeiro), até preencher as vagas livres.
    # Numa onda de sinais, todos entram no mesmo ciclo (como no backtest), não um a cada 5 min.
    free_slots = cfg.max_positions - len(open_positions)
    candidates: list[Signal] = []
    for sig in signals:
        if sig.symbol in busy:  # bloqueio de duplicatas
            _cancel(sig.id, "Já existe posição aberta para o símbolo")
            continue
        if sig.symbol in cooling:
            _cancel(sig.id, f"Par em cooldown após stop (até {cooling[sig.symbol]:%H:%M} UTC)")
            continue
        if per_direction.get(sig.direction, 0) >= cfg.max_same_direction:
            continue  # fica aprovado; pode ser executado quando a exposição diminuir
        if sig.confidence_score < cfg.min_execute_score:
            break  # lista ordenada: os próximos também estão abaixo
        candidates.append(sig)
        per_direction[sig.direction] = per_direction.get(sig.direction, 0) + 1
        if len(candidates) >= free_slots:
            break
    if not candidates:
        return {"success": True, "skipped": "Nenhum sinal elegível (duplicata, cooldown, exposição ou score)"}

    # Notícias avaliadas ANTES de executar (o ajuste de score muda a ordem)
    approved: list[tuple[Signal, float]] = []
    if cfg.news_check_enabled:
        with ThreadPoolExecutor(max_workers=max(1, cfg.news_top_candidates)) as pool:
            results = list(pool.map(
                lambda s: check_news_before_trade(s.symbol, s.direction, cfg.news_block_generic), candidates))
        for sig, news in zip(candidates, results):
            if not news["allow"]:
                _cancel(sig.id, f"Notícia negativa: {news['summary']}", news)
                botlog.warn(MODULE, f"{sig.symbol}: cancelado por notícia ({news.get('severity')}, "
                                    f"específica={news.get('coin_specific')}): {news['summary']}")
                continue
            adjusted = sig.confidence_score + news["score_adjustment"]
            with session_scope() as db:
                row = db.get(Signal, sig.id)
                row.news_result = news
                row.confidence_score = adjusted
            botlog.info(MODULE, f"{sig.symbol}: notícias {news['impact']} (+{news['score_adjustment']}): {news['summary']}")
            approved.append((sig, adjusted))
        if not approved:
            return {"success": True, "skipped": "Todos os candidatos foram cancelados por notícia"}
    else:
        approved = [(s, s.confidence_score) for s in candidates]

    executed = []
    for sig, score in sorted(approved, key=lambda x: -x[1]):
        botlog.info(MODULE, f"Executando sinal #{sig.id} {sig.symbol} {sig.direction} score {score:.0f} "
                            f"(amplitude {breadth})")
        result = execute_and_mark(sig.id)
        if result.get("success"):
            botlog.info(MODULE, f"{sig.symbol}: executado com sucesso (trade #{result['trade_id']})")
        else:
            botlog.error(MODULE, f"{sig.symbol}: execução falhou: {result.get('error')}")
        executed.append({"signal_id": sig.id, "symbol": sig.symbol, "success": bool(result.get("success")),
                         "error": result.get("error")})
    return {"success": True, "breadth": breadth, "executed": executed}
