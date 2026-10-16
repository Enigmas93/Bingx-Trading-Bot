from datetime import timedelta
from threading import Lock

from apscheduler.schedulers.background import BackgroundScheduler

from . import botlog
from .auto_execute import run_auto_execute
from .maintenance import run_maintenance
from .models import utcnow
from .quant import performance as quant_performance
from .quant import reports as quant_reports
from .quant import registry as quant_registry
from .quant import research as quant_research
from .quant.engine import run_engine
from .quant.monitor import run_monitor
from .scanner import run_scan
from .sync import run_sync

SYNC_SECONDS = 30

JOBS = {
    "scanner": run_scan,
    "auto_execute": run_auto_execute,
    "sync": run_sync,
    "maintenance": run_maintenance,
    "quant_engine": run_engine,
    "quant_monitor": run_monitor,
    "quant_performance": quant_performance.refresh_demo,
    "quant_report": quant_reports.generate,
    "quant_research": quant_research.run_research,
}
# O ciclo legado (scanner + auto_execute) continua disponível para execução MANUAL, mas não é mais agendado:
# o motor quant multi-estratégia o substitui (inclui a estratégia original como PULLBACK_TREND).
SCHEDULED_LEGACY = False

job_status: dict[str, dict] = {name: {"running": False, "last_run": None, "last_result": None} for name in JOBS}
_locks = {name: Lock() for name in JOBS}
scheduler = BackgroundScheduler(timezone="UTC")


def run_job(name: str) -> dict:
    lock = _locks[name]
    if not lock.acquire(blocking=False):
        return {"skipped": f"{name} já está em execução"}
    status = job_status[name]
    status["running"] = True
    try:
        result = JOBS[name]()
    except Exception as exc:
        botlog.error(name, f"Erro no job: {exc}")
        result = {"error": str(exc)}
    finally:
        status["running"] = False
        status["last_run"] = utcnow().isoformat()
        lock.release()
    status["last_result"] = result
    return result


def run_cycle() -> None:
    """Scanner logo após o fechamento do candle e, em seguida, a auto-execução."""
    run_job("scanner")
    run_job("auto_execute")


def _quant_cycle() -> None:
    """A cada 5 min: o motor só processa quando há um candle de 1h novo (cada candle uma única vez)."""
    result = run_job("quant_engine")
    processed = isinstance(result, dict) and any(
        isinstance(r, dict) and "candle_time" in r and not r.get("skipped") for r in result.values())
    if processed:
        run_job("quant_performance")


def _sync_cycle() -> None:
    # Só o monitor do motor quant: ele acompanha os trades, confirma fechamentos e concilia com a corretora.
    # A sincronização antiga (tabela de posições do sistema anterior) não é mais agendada: ela registrava como
    # "externas" posições que eram do motor.
    run_job("quant_monitor")


def _research_if_missing() -> None:
    """Roda a pesquisa dos timeframes que ainda nunca foram estudados (primeira subida ou timeframe novo)."""
    missing = [tf for tf in quant_registry.TIMEFRAMES if quant_research.latest_run(tf) is None]
    if missing:
        quant_research.run_research(timeframes=missing)


def start_scheduler() -> None:
    now = utcnow()
    if SCHEDULED_LEGACY:
        scheduler.add_job(run_cycle, "cron", minute="*/5", second=5, id="cycle",
                          max_instances=1, coalesce=True, misfire_grace_time=120)
    scheduler.add_job(_quant_cycle, "cron", minute="*/5", second=20, id="quant_cycle",
                      max_instances=1, coalesce=True, misfire_grace_time=240)
    scheduler.add_job(_sync_cycle, "interval", seconds=SYNC_SECONDS, id="sync",
                      next_run_time=now + timedelta(seconds=10), max_instances=1, coalesce=True)
    if SCHEDULED_LEGACY:
        scheduler.add_job(run_job, "interval", args=["maintenance"], minutes=15, id="maintenance",
                          next_run_time=now + timedelta(seconds=60), max_instances=1, coalesce=True)
    scheduler.add_job(run_job, "cron", args=["quant_report"], hour=0, minute=5, id="quant_report",
                      max_instances=1, coalesce=True)
    scheduler.add_job(run_job, "cron", args=["quant_research"], day_of_week="sun", hour=1, minute=30,
                      id="quant_research", max_instances=1, coalesce=True)
    scheduler.add_job(_research_if_missing, "date", run_date=now + timedelta(seconds=90), id="research_bootstrap")
    scheduler.start()
    botlog.info("scheduler", "Agendador iniciado: motor quant a cada 5 min (1h, 15m e 5m; cada candle processado uma vez), "
                             f"monitor DEMO + conciliação com a corretora a cada {SYNC_SECONDS}s, relatório diário 00:05 UTC, "
                             "pesquisa semanal (domingo 01:30 UTC)")


def next_runs() -> dict[str, str | None]:
    def nxt(job_id: str) -> str | None:
        job = scheduler.get_job(job_id) if scheduler.running else None
        return job.next_run_time.isoformat() if job and job.next_run_time else None

    quant = nxt("quant_cycle")
    return {"scanner": nxt("cycle"), "auto_execute": nxt("cycle"), "sync": nxt("sync"),
            "maintenance": nxt("maintenance"), "quant_engine": quant, "quant_monitor": nxt("sync"),
            "quant_report": nxt("quant_report"), "quant_research": nxt("quant_research")}
