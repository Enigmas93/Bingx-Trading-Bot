"""Log que vai para o console e para a tabela bot_logs (exibida no painel)."""

import logging

from .database import session_scope
from .models import BotLog

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def log(module: str, level: str, message: str) -> None:
    level = level.upper()
    logging.getLogger(module).log(getattr(logging, level, logging.INFO), message)
    try:
        with session_scope() as db:
            db.add(BotLog(module=module, level=level, message=message[:4000]))
    except Exception:  # log nunca deve derrubar o bot
        logging.getLogger("botlog").exception("Falha ao gravar log no banco")


def info(module: str, message: str) -> None:
    log(module, "INFO", message)


def warn(module: str, message: str) -> None:
    log(module, "WARNING", message)


def error(module: str, message: str) -> None:
    log(module, "ERROR", message)


def critical(module: str, message: str) -> None:
    log(module, "CRITICAL", message)
