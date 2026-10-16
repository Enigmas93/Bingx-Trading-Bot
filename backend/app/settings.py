import os

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


BINGX_API_KEY = os.getenv("BINGX_API_KEY", "")
BINGX_SECRET_KEY = os.getenv("BINGX_SECRET_KEY") or os.getenv("BINGX_API_SECRET", "")
# BINGX_DEMO e BINGX_TESTNET são sinônimos (conta demo VST)
BINGX_DEMO = _bool("BINGX_DEMO", _bool("BINGX_TESTNET", True))

# Esta versão é EXCLUSIVAMENTE de validação: execução só na conta DEMO.
# O sistema recusa executar se DEMO_MODE != true ou LIVE_TRADING == true (ver app/quant/safety.py).
DEMO_MODE = _bool("DEMO_MODE", True)
LIVE_TRADING = _bool("LIVE_TRADING", False)

NEWS_ENABLED = _bool("NEWS_ENABLED", True)
NVIDIA_API_KEY = os.getenv("NVIDIA_API_KEY", "").strip()
NEWS_LLM_MODEL = os.getenv("NEWS_LLM_MODEL", "nvidia/nemotron-3-super-120b-a12b")
COINMARKETCAL_API_KEY = os.getenv("COINMARKETCAL_API_KEY", "").strip()
BLS_API_KEY = os.getenv("BLS_API_KEY", "").strip()
BEA_API_KEY = os.getenv("BEA_API_KEY", "").strip()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./bot.db")
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))
DASHBOARD_ORIGIN = os.getenv("DASHBOARD_ORIGIN", "http://localhost:3000")
# Token exigido em todas as rotas /api quando definido (obrigatório se o backend for exposto por túnel para o
# painel hospedado na Vercel). O painel envia no header X-Bot-Token a partir do servidor (nunca no navegador).
BOT_API_TOKEN = os.getenv("BOT_API_TOKEN", "")
SCHEDULER_ENABLED = _bool("SCHEDULER_ENABLED", True)
