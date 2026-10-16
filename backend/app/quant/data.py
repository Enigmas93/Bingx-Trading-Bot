"""Separação fixa entre DADOS (conta real) e EXECUÇÃO (conta demo).

Regra do sistema quant:
- TODA a análise (universo/ranking por volume, candles de todos os timeframes, regime do BTC, spread, funding,
  preço de referência do sinal, MFE/MAE e sinais de saída) usa os dados públicos da conta REAL
  (open-api.bingx.com) por meio de um cliente SOMENTE LEITURA e sem chaves: ele não consegue assinar requisições,
  portanto não acessa conta nem envia ordens.
- A conta DEMO (open-api-vst.bingx.com) é usada só para o que é da execução: ordens, posições, saldo, PnL realizado
  e regras do contrato (passo de quantidade/preço) da conta onde a ordem é enviada.
Não há opção para trocar a fonte de análise: quando a estratégia for validada, migrar para a conta real muda apenas
o adaptador de execução — a análise já é a mesma.
"""

from ..bingx import LIVE_URL, BingXClient, get_client, get_live_data_client

DATA_SOURCE = {"name": "BingX conta REAL (dados públicos)", "endpoint": LIVE_URL, "access": "somente leitura, sem chaves"}


def market() -> BingXClient:
    """Cliente de dados de mercado — sempre a conta real, somente leitura."""
    c = get_live_data_client()
    assert c.read_only and c.base_url == LIVE_URL, "fonte de análise deve ser a conta real em modo somente leitura"
    return c


def execution() -> BingXClient:
    """Cliente da conta de execução (DEMO nesta versão; as travas de safety.py e do BingXClient garantem isso)."""
    return get_client()


INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}


def closed_klines(symbol: str, interval: str, limit: int = 300) -> list[dict]:
    """Candles FECHADOS da conta real. Descarta só os que ainda não fecharam pelo relógio da corretora (na virada
    do candle a API pode ainda não ter criado o novo — descartar "sempre o último" perderia um candle fechado)."""
    import time
    mk = market()
    kl = mk.get_klines(symbol, interval, limit + 1)
    if hasattr(mk, "sync_time") and time.time() - getattr(mk, "_time_synced_at", 0) > 600:
        try:
            mk.sync_time()   # relógio do PC pode estar adiantado/atrasado: usa o horário da BingX
            mk._time_synced_at = time.time()
        except Exception:
            pass
    now_ms = int(time.time() * 1000) + getattr(mk, "_time_offset_ms", 0)
    step = INTERVAL_MS.get(interval)
    if step is None:
        return kl[:-1]
    closed = [k for k in kl if k["time"] + step <= now_ms]
    return closed[-limit:]


def describe() -> dict:
    ex = execution()
    return {"analysis_data": DATA_SOURCE,
            "execution": {"name": "BingX conta DEMO (VST)" if ex.base_url != LIVE_URL else "BingX conta REAL",
                          "endpoint": ex.base_url}}
