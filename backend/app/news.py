"""checkNewsBeforeTrade: IA (NVIDIA NIM) analisa notícias, eventos e macro antes de executar.

Fontes:
- RSS cripto (Cointelegraph, CoinDesk, Decrypt): manchetes das últimas 2h
- CoinMarketCal: eventos da moeda (2h atrás até 48h à frente)
- BLS: CPI, desemprego e payroll dos EUA
- BEA: PIB real dos EUA
"""

import calendar
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from . import botlog, settings

MODULE = "news"
WINDOW_SECONDS = 2 * 3600
EVENTS_AHEAD = timedelta(hours=48)
MAX_SCORE_ADJUSTMENT = 10
NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
COINMARKETCAL_URL = "https://api.coinmarketcal.com/v2/events"
BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BEA_URL = "https://apps.bea.gov/api/data"

FEEDS = [
    "https://cointelegraph.com/rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://decrypt.co/feed",
]

COIN_NAMES = {
    "BTC": ["bitcoin"], "ETH": ["ethereum", "ether"], "SOL": ["solana"], "XRP": ["ripple", "xrp"],
    "BNB": ["binance coin", "bnb"], "DOGE": ["dogecoin"], "ADA": ["cardano"], "AVAX": ["avalanche"],
    "LINK": ["chainlink"], "DOT": ["polkadot"], "TRX": ["tron"], "TON": ["toncoin"], "LTC": ["litecoin"],
    "SHIB": ["shiba inu"], "SUI": ["sui"], "APT": ["aptos"], "ARB": ["arbitrum"], "OP": ["optimism"],
    "NEAR": ["near protocol"], "PEPE": ["pepe"], "UNI": ["uniswap"], "AAVE": ["aave"], "ATOM": ["cosmos"],
    "FIL": ["filecoin"], "INJ": ["injective"], "SEI": ["sei"], "TIA": ["celestia"], "WIF": ["dogwifhat"],
    "WLD": ["worldcoin", "world network"],
}

_HTTP_HEADERS = {"User-Agent": "Mozilla/5.0"}
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, ttl: float, loader):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    value = loader()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


# --------------------------------------------------------------------- RSS
def _fetch_recent_news() -> list[dict]:
    cutoff = time.time() - WINDOW_SECONDS
    items: list[dict] = []
    with httpx.Client(timeout=10.0, follow_redirects=True, headers=_HTTP_HEADERS) as http:
        for url in FEEDS:
            try:
                feed = feedparser.parse(http.get(url).content)
            except Exception as exc:
                botlog.warn(MODULE, f"Falha ao ler feed {url}: {exc}")
                continue
            for e in feed.entries:
                published = e.get("published_parsed") or e.get("updated_parsed")
                if not published:
                    continue
                ts = calendar.timegm(published)
                if ts < cutoff:
                    continue
                items.append({
                    "title": e.get("title", ""),
                    "summary": (e.get("summary", "") or "")[:400],
                    "source": feed.feed.get("title", url),
                    "published_ts": ts,
                })
    items.sort(key=lambda x: x["published_ts"], reverse=True)
    return items


def _relevant(items: list[dict], base: str) -> tuple[list[dict], list[dict]]:
    terms = [base.lower()] + COIN_NAMES.get(base.upper(), [])
    coin, general = [], []
    for it in items:
        text = f" {it['title']} {it['summary']} ".lower()
        (coin if any(f" {t}" in text or f"${t}" in text for t in terms) else general).append(it)
    return coin, general


# ----------------------------------------------------------- CoinMarketCal
def _load_events() -> list[dict]:
    """Todos os eventos de ontem até daqui a 3 dias (paginado pelo cursor)."""
    today = datetime.now(timezone.utc).date()
    params = {"from": (today - timedelta(days=1)).isoformat(), "to": (today + timedelta(days=3)).isoformat(),
              "limit": 100}
    events: list[dict] = []
    with httpx.Client(timeout=15.0) as http:
        for _ in range(10):
            resp = http.get(COINMARKETCAL_URL, headers={"x-api-key": settings.COINMARKETCAL_API_KEY}, params=params)
            resp.raise_for_status()
            payload = resp.json()
            events.extend(payload.get("data", []))
            cursor = (payload.get("meta") or {}).get("cursor")
            if not cursor or not payload.get("data"):
                break
            params["cursor"] = cursor
    return events


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _coin_events(base: str) -> list[dict]:
    if not settings.COINMARKETCAL_API_KEY:
        return []
    events = _cached("coinmarketcal", 1800, _load_events)
    now = datetime.now(timezone.utc)
    start, end = now - timedelta(seconds=WINDOW_SECONDS), now + EVENTS_AHEAD
    names = {n.lower() for n in COIN_NAMES.get(base.upper(), [])}
    selected = []
    for ev in events:
        coins = ev.get("coins") or []
        if not any(str(c.get("symbol", "")).lower() == base.lower() or str(c.get("name", "")).lower() in names
                   for c in coins):
            continue
        ev_start = _parse_dt(ev.get("date"))
        ev_end = _parse_dt(ev.get("dateEnd")) or ev_start
        if ev_start is None or ev_start > end or (ev_end and ev_end < start):
            continue
        selected.append(ev)
    return selected


# --------------------------------------------------------------- BLS / BEA
def _load_bls() -> list[str]:
    year = datetime.now(timezone.utc).year
    resp = httpx.post(BLS_URL, timeout=20.0, json={
        "seriesid": ["CUUR0000SA0", "LNS14000000", "CES0000000001"],
        "startyear": str(year - 1), "endyear": str(year), "registrationkey": settings.BLS_API_KEY,
    })
    resp.raise_for_status()
    series = {s["seriesID"]: s["data"] for s in resp.json()["Results"]["series"]}
    lines = []

    cpi = series.get("CUUR0000SA0", [])
    if len(cpi) >= 13:
        latest, year_ago = cpi[0], next((d for d in cpi if d["year"] == str(int(cpi[0]["year"]) - 1)
                                         and d["period"] == cpi[0]["period"]), None)
        if year_ago:
            yoy = (float(latest["value"]) / float(year_ago["value"]) - 1) * 100
            mom = (float(cpi[0]["value"]) / float(cpi[1]["value"]) - 1) * 100
            lines.append(f"CPI EUA {latest['periodName']}/{latest['year']}: {yoy:.2f}% a/a, {mom:+.2f}% m/m (BLS)")

    unemp = series.get("LNS14000000", [])
    if len(unemp) >= 2:
        lines.append(f"Desemprego EUA {unemp[0]['periodName']}/{unemp[0]['year']}: {unemp[0]['value']}% "
                     f"(anterior {unemp[1]['value']}%) (BLS)")

    payroll = series.get("CES0000000001", [])
    if len(payroll) >= 2:
        change = (float(payroll[0]["value"]) - float(payroll[1]["value"])) * 1000
        lines.append(f"Payroll EUA {payroll[0]['periodName']}/{payroll[0]['year']}: {change:+,.0f} empregos (BLS)")
    return lines


def _load_bea() -> list[str]:
    year = datetime.now(timezone.utc).year
    resp = httpx.get(BEA_URL, timeout=20.0, params={
        "UserID": settings.BEA_API_KEY, "method": "GetData", "DataSetName": "NIPA", "TableName": "T10101",
        "Frequency": "Q", "Year": f"{year - 1},{year}", "ResultFormat": "JSON",
    })
    resp.raise_for_status()
    rows = [r for r in resp.json()["BEAAPI"]["Results"]["Data"] if r.get("LineNumber") == "1"]
    if not rows:
        return []
    rows.sort(key=lambda r: r["TimePeriod"])
    latest = rows[-1]
    prev = f" (trimestre anterior {rows[-2]['DataValue']}%)" if len(rows) > 1 else ""
    return [f"PIB real EUA {latest['TimePeriod']}: {latest['DataValue']}% anualizado{prev} (BEA)"]


def _macro_context() -> list[str]:
    lines: list[str] = []
    for key, enabled, loader in (("bls", settings.BLS_API_KEY, _load_bls), ("bea", settings.BEA_API_KEY, _load_bea)):
        if not enabled:
            continue
        try:
            lines.extend(_cached(key, 6 * 3600, loader))
        except Exception as exc:
            botlog.warn(MODULE, f"Falha ao buscar dados macro ({key.upper()}): {exc}")
    return lines


# ------------------------------------------------------------------ NVIDIA
def _ask_llm(prompt: str, attempts: int = 2) -> dict:
    """Chama a IA; repete uma vez se a resposta vier vazia ou sem JSON."""
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return _ask_llm_once(prompt)
        except ValueError as exc:  # resposta vazia/sem JSON: costuma funcionar na segunda tentativa
            last_exc = exc
    raise last_exc


def _ask_llm_once(prompt: str) -> dict:
    resp = httpx.post(
        NVIDIA_URL, timeout=60.0,
        headers={"Authorization": f"Bearer {settings.NVIDIA_API_KEY}", "Accept": "application/json"},
        json={
            "model": settings.NEWS_LLM_MODEL,
            "messages": [
                {"role": "system", "content": "Você é um analista de risco. Responda APENAS com um objeto JSON válido."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 800,
        },
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"].get("content") or ""
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    match = re.search(r"\{.*\}", content, flags=re.S)
    if not match:
        raise ValueError(f"Resposta sem JSON: {content[:200]!r}")
    return json.loads(match.group(0))


# ------------------------------------------------------------------- main
def check_news_before_trade(symbol: str, direction: str, block_generic: bool = True) -> dict:
    """Retorna {allow, impact, score_adjustment, summary, news_count, sources}.

    Notícia negativa para a operação -> allow=False (trade cancelado).
    Notícia positiva -> score_adjustment > 0. Em erro/sem IA configurada, o trade segue sem ajuste.
    """
    base = symbol.split("-")[0]
    neutral = {"allow": True, "impact": "neutral", "score_adjustment": 0, "news_count": 0}

    if not settings.NEWS_ENABLED:
        return {**neutral, "summary": "NEWS_ENABLED=false: checagem de notícias desativada"}
    if not settings.NVIDIA_API_KEY:
        return {**neutral, "summary": "NVIDIA_API_KEY não configurada: checagem de notícias ignorada"}

    try:
        coin_news, general_news = _relevant(_fetch_recent_news(), base)
    except Exception as exc:
        botlog.warn(MODULE, f"Erro ao buscar notícias RSS: {exc}")
        coin_news, general_news = [], []
    try:
        events = _coin_events(base)
    except Exception as exc:
        botlog.warn(MODULE, f"Erro ao buscar eventos CoinMarketCal: {exc}")
        events = []
    macro = _macro_context()

    headlines = coin_news[:15] + general_news[:10]
    sources = {"rss": len(headlines), "coinmarketcal": len(events), "macro": len(macro)}
    if not headlines and not events:
        return {**neutral, "sources": sources, "summary": "Nenhuma notícia ou evento relevante nas últimas 2h"}

    news_text = "\n".join(
        f"- [{time.strftime('%H:%M UTC', time.gmtime(n['published_ts']))}] ({n['source']}) {n['title']}: {n['summary']}"
        for n in headlines
    ) or "- (nenhuma manchete nas últimas 2h)"
    events_text = "\n".join(
        f"- {ev.get('displayedDate') or ev.get('date')}: {ev.get('title')}"
        + (f" - {ev['description'][:200]}" if ev.get("description") else "")
        + (f" [impacto CoinMarketCal: {ev['impact']}]" if ev.get("impact") else "")
        for ev in events
    ) or "- (nenhum evento agendado)"
    macro_text = "\n".join(f"- {line}" for line in macro) or "- (indisponível)"

    prompt = (
        f"Um bot de trading de futuros perpétuos vai abrir uma posição {direction} em {symbol} (ativo: {base}).\n\n"
        f"MANCHETES CRIPTO (últimas 2h):\n{news_text}\n\n"
        f"EVENTOS AGENDADOS DE {base} (CoinMarketCal, de 2h atrás até 48h à frente):\n{events_text}\n\n"
        f"CONTEXTO MACRO EUA (últimos dados oficiais, apenas contexto de fundo):\n{macro_text}\n\n"
        f"Avalie o impacto ESPECÍFICO para uma posição {direction} em {base} nas próximas horas. "
        f"Ignore o que for irrelevante para essa operação.\n"
        f"- impact = \"negative\" se algo relevante for CONTRA a operação {direction} (hack, delisting, "
        f"unlock grande de tokens, processo regulatório, evento macro adverso, notícia que contradiz a direção).\n"
        f"- impact = \"positive\" se algo relevante FAVORECER a operação.\n"
        f"- impact = \"neutral\" se não houver nada relevante ou o efeito for incerto.\n"
        f"- coin_specific = true se a notícia/evento decisivo for sobre {base} especificamente "
        f"(false se for só sentimento geral do mercado ou do BTC).\n"
        f"- severity = \"high\" apenas para eventos capazes de mover o mercado com força (hack, delisting, "
        f"decisão regulatória, dado macro muito fora do esperado); \"medium\" ou \"low\" nos demais casos.\n"
        f"Responda SOMENTE com JSON no formato:\n"
        f"{{\"impact\": \"negative|neutral|positive\", \"coin_specific\": true|false, "
        f"\"severity\": \"low|medium|high\", \"score_adjustment\": 0-{MAX_SCORE_ADJUSTMENT}, "
        f"\"summary\": \"uma frase curta em português\"}}\n"
        f"score_adjustment só é maior que 0 quando impact for positive."
    )

    try:
        data = _ask_llm(prompt)
    except Exception as exc:
        botlog.warn(MODULE, f"Erro na análise de notícias via NVIDIA: {exc}")
        return {**neutral, "news_count": len(headlines), "sources": sources, "summary": f"Erro na IA: {exc}"}

    impact = str(data.get("impact", "neutral")).lower()
    if impact not in ("negative", "neutral", "positive"):
        impact = "neutral"
    try:
        raw_adjustment = int(data.get("score_adjustment", 0))
    except (TypeError, ValueError):
        raw_adjustment = 0
    adjustment = max(0, min(MAX_SCORE_ADJUSTMENT, raw_adjustment)) if impact == "positive" else 0
    coin_specific = bool(data.get("coin_specific", False))
    severity = str(data.get("severity", "low")).lower()
    # block_generic=False: só cancela por notícia específica da moeda ou de alto impacto.
    blocking = impact == "negative" and (block_generic or coin_specific or severity == "high")
    return {
        "allow": not blocking,
        "impact": impact,
        "coin_specific": coin_specific,
        "severity": severity,
        "generic_negative": impact == "negative" and not blocking,
        "score_adjustment": adjustment,
        "summary": str(data.get("summary", "")),
        "news_count": len(headlines),
        "sources": sources,
    }


# ------------------------------------------------- avaliação "sombra" (#20)
def shadow_outcome(signal, klines_loader) -> str | None:
    """O que teria acontecido com um sinal cancelado: 'win' (TP1 antes do SL), 'loss' ou 'open' (nenhum dos dois).

    klines_loader(symbol, start_ms) -> candles de 5m a partir da criação do sinal.
    Retorna None se ainda não há candles suficientes.
    """
    created = signal.created_at if signal.created_at.tzinfo else signal.created_at.replace(tzinfo=timezone.utc)
    candles = klines_loader(signal.symbol, int(created.timestamp() * 1000))
    if not candles:
        return None
    is_long = signal.direction == "LONG"
    for k in candles:
        hit_sl = k["low"] <= signal.stop_loss if is_long else k["high"] >= signal.stop_loss
        hit_tp = k["high"] >= signal.tp1 if is_long else k["low"] <= signal.tp1
        if hit_sl:  # conservador: se os dois no mesmo candle, conta como stop
            return "loss"
        if hit_tp:
            return "win"
    return "open" if len(candles) >= 280 else None
