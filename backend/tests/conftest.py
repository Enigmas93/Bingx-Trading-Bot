"""Testes isolados: banco temporário, agendador desligado, sem rede."""

import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="quant_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp, 'test.db').as_posix()}"
os.environ["SCHEDULER_ENABLED"] = "false"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from app.database import init_db  # noqa: E402

init_db()


def make_candles(closes, vol=None, wick=0.002, start=1_700_000_000_000, step=3_600_000, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        o = prev
        hi = max(o, c) * (1 + abs(rng.normal(0, wick)))
        lo = min(o, c) * (1 - abs(rng.normal(0, wick)))
        v = float(vol[i]) if vol is not None else float(1000 * (1 + abs(rng.normal(0, 0.3))))
        out.append({"time": start + i * step, "open": float(o), "high": float(hi), "low": float(lo),
                    "close": float(c), "volume": v})
        prev = c
    return out


def random_walk(n=3000, seed=7, drift=0.0, vol=0.012):
    rng = np.random.default_rng(seed)
    # regimes alternados de tendência/lateral/volatilidade para gerar sinais de todas as famílias
    r = rng.normal(drift, vol, n)
    for k in range(0, n, 400):
        r[k:k + 150] += rng.choice([-1, 1]) * 0.004
        r[k + 200:k + 260] *= 2.5
    closes = 100 * np.exp(np.cumsum(r))
    volume = 1000 * np.exp(rng.normal(0, 0.5, n))
    return closes, volume


def reflect(candles):
    """Espelho exato de preço (p' = K - p): troca topo↔fundo, alta↔baixa; indicadores lineares espelham."""
    k = 2.2 * max(c["high"] for c in candles)
    return [{"time": c["time"], "open": k - c["open"], "close": k - c["close"], "high": k - c["low"],
             "low": k - c["high"], "volume": c["volume"]} for c in candles]


@pytest.fixture
def rw_candles():
    closes, vol = random_walk()
    return make_candles(closes, vol)
