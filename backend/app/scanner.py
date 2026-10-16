"""advancedMarketScanner: analisa o mercado e gera sinais 'approved'.

Os dados de mercado (ranking por volume, candles, spread, funding, tendências) vêm da conta REAL quando
market_data_live está ligado; a execução continua na conta configurada (demo). Assim a análise na demo
é a mesma que rodará na conta real.
"""

import re
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from . import botlog
from .bingx import BingXClient, get_client, get_market_client
from .common import expire_stale_signals, get_config, is_blocked_hour, open_symbols
from .database import session_scope
from .models import Signal
from .risk import cooldown_symbols
from .strategy import StrategyParams, analyze, compute_levels, cost_filter, regime_filter, trend_of

MODULE = "scanner"
# Sintéticos não-cripto da BingX (petróleo, ouro, índices...): ex. NCCO1OILBRENT2USD-USDT.
# A API rejeita ordens neles em modo one-way (code 101414).
NON_CRYPTO = re.compile(r"^NC[A-Z0-9]+USD-USDT$")
HTF_CANDLES = 120
WORKERS = 4  # pares analisados em paralelo (respeitando o limite de requisições da BingX)


def _evaluate(market: BingXClient, t: dict, cfg, params: StrategyParams, btc_trend: str | None) -> tuple[str, dict | None]:
    """Analisa um par. Retorna (categoria da estatística, dados do sinal ou None)."""
    symbol = t["symbol"]
    bid, ask = market.get_book_top(symbol)
    spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
    if spread_pct > cfg.max_spread_pct:
        return "spread", None

    funding = market.get_funding_rate(symbol)
    if abs(funding) * 100 > cfg.max_funding_pct:
        return "funding", None

    candles = market.get_klines(symbol, cfg.timeframe, 201)[:-1]  # descarta candle em formação
    result = analyze(candles, params)
    if not result["passed"]:
        return "rejeitados", None

    direction = result["direction"]
    htf_trend = None
    if params.htf_filter_enabled:
        htf_trend = trend_of(market.get_klines(symbol, cfg.htf_timeframe, HTF_CANDLES + 1)[:-1])
    if regime_filter(direction, htf_trend, btc_trend, symbol == "BTC-USDT", params):
        return "regime", None

    snap = result["indicators"]
    entry = market.get_price(symbol)
    levels = compute_levels(direction, entry, snap["atr"], snap["support"], snap["resistance"],
                            result["pullback_extreme"], params)
    if levels is None:
        return "stop_largo", None
    if cost_filter(entry, levels["stop_loss"], spread_pct, params):
        return "custo", None

    snap.update({"spread_pct": round(spread_pct, 4), "funding_rate_pct": round(funding * 100, 5),
                 "quote_volume_24h": t["quote_volume"], "htf_trend": htf_trend, "btc_trend": btc_trend,
                 "stop_pct": round(abs(entry - levels["stop_loss"]) / entry * 100, 4),
                 "data_source": "real" if market is not get_client() else "conta",
                 "tp_split": {"tp1": 50, "tp2": 28.6, "tp3": 21.4}})
    return "sinais", {"symbol": symbol, "direction": direction, "entry": entry, "levels": levels,
                      "result": result, "snap": snap}


def run_scan() -> dict:
    client = get_client()
    cfg = get_config()
    params = StrategyParams.from_config(cfg)
    market = get_market_client(cfg.market_data_live)

    if is_blocked_hour(cfg):
        msg = f"Horário de baixa liquidez ({cfg.block_hour_start:02d}:00-{cfg.block_hour_end:02d}:59 UTC): scan bloqueado"
        botlog.info(MODULE, msg)
        return {"skipped": msg}

    expired = expire_stale_signals(cfg)
    tradable = set(client.get_contracts())  # pares que existem na conta onde as ordens são executadas

    tickers = []
    for t in market.get_tickers():
        symbol = str(t.get("symbol", ""))
        if not symbol.endswith("-USDT") or NON_CRYPTO.match(symbol):
            continue
        last = float(t.get("lastPrice") or 0)
        quote_vol = float(t.get("quoteVolume") or 0) or float(t.get("volume") or 0) * last
        if last > 0 and quote_vol >= cfg.min_volume_usdt:
            tickers.append({"symbol": symbol, "price": last, "quote_volume": quote_vol})
    tickers.sort(key=lambda x: x["quote_volume"], reverse=True)
    ranked = tickers[: cfg.scan_top_n]
    candidates = [t for t in ranked if t["symbol"] in tradable]

    btc_trend = None
    if params.btc_filter_enabled:
        try:
            btc_trend = trend_of(market.get_klines("BTC-USDT", cfg.htf_timeframe, HTF_CANDLES + 1)[:-1])
        except Exception as exc:
            botlog.warn(MODULE, f"Não foi possível calcular a tendência do BTC: {exc}")

    busy = open_symbols(client)
    cooling = cooldown_symbols(cfg)
    stats = {"ranking": len(ranked), "fora_da_conta": len(ranked) - len(candidates), "analisados": 0, "sinais": 0,
             "spread": 0, "funding": 0, "posicao_existente": 0, "cooldown": 0, "rejeitados": 0, "regime": 0,
             "custo": 0, "stop_largo": 0, "erros": 0, "expirados": expired}

    to_analyze = []
    for t in candidates:
        if t["symbol"] in busy:
            stats["posicao_existente"] += 1
        elif t["symbol"] in cooling:
            stats["cooldown"] += 1
        else:
            to_analyze.append(t)

    def safe_eval(t):
        try:
            return t, _evaluate(market, t, cfg, params, btc_trend)
        except Exception as exc:
            return t, ("erros", str(exc))

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outcomes = list(pool.map(safe_eval, to_analyze))

    created: list[str] = []
    for t, (category, payload) in outcomes:
        stats[category] += 1
        if category not in ("spread", "funding"):
            stats["analisados"] += 1
        if category == "erros":
            botlog.warn(MODULE, f"{t['symbol']}: erro na análise: {payload}")
            continue
        if category != "sinais":
            continue
        res, snap = payload["result"], payload["snap"]
        with session_scope() as db:
            for old in db.scalars(select(Signal).where(Signal.symbol == payload["symbol"], Signal.status == "approved")):
                old.status = "expired"
                old.status_reason = "Substituído por sinal mais recente"
            db.add(Signal(
                symbol=payload["symbol"], direction=payload["direction"], status="approved",
                confidence_score=res["score"], min_score_required=res["min_score"],
                adx=snap["adx"], atr=snap["atr"], entry_price=payload["entry"], indicators=snap,
                score_breakdown=res["breakdown"], status_reason=res["reason"], **payload["levels"],
            ))
        created.append(f"{payload['symbol']} {payload['direction']} ({res['score']:.0f}, ADX {snap['adx']:.0f})")

    source = "conta real" if market is not client else "conta configurada"
    botlog.info(MODULE, f"Scan {cfg.timeframe} em {len(candidates)} pares (dados da {source}, BTC {btc_trend or '-'}): "
                        f"{stats}" + (f" | Novos sinais: {', '.join(created)}" if created else ""))
    return {"stats": stats, "signals": created, "btc_trend": btc_trend}
