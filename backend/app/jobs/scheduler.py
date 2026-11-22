"""In-process scheduler (APScheduler) for the daily import (IMP-5/6) and daily digest (ALR-3).

In larger deployments run `python -m app.jobs.worker` as a separate container and set
SCHEDULER_ENABLED=false on the web app.
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings

log = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def daily_import():
    from app.ingestion import sources

    runs = sources.scheduled_pull(actor="scheduler")
    log.info("scheduled import finished: %s run(s)", len(runs))


def daily_digest():
    from app.jobs import digest

    digest.send_all()


def build(scheduler_cls=BackgroundScheduler):
    s = get_settings()
    sch = scheduler_cls(timezone=s.IMPORT_TIMEZONE)
    sch.add_job(daily_import, CronTrigger.from_crontab(s.IMPORT_CRON, timezone=s.IMPORT_TIMEZONE), id="daily_import", replace_existing=True, max_instances=1, coalesce=True)
    sch.add_job(daily_digest, CronTrigger.from_crontab("30 7 * * 1-5", timezone=s.IMPORT_TIMEZONE), id="daily_digest", replace_existing=True, max_instances=1, coalesce=True)
    return sch


def start() -> None:
    global _scheduler
    if _scheduler is None and get_settings().SCHEDULER_ENABLED:
        _scheduler = build()
        _scheduler.start()
        log.info("scheduler started (import cron %s %s)", get_settings().IMPORT_CRON, get_settings().IMPORT_TIMEZONE)


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_runs() -> dict:
    if _scheduler is None:
        return {}
    return {j.id: j.next_run_time.isoformat() if j.next_run_time else None for j in _scheduler.get_jobs()}
