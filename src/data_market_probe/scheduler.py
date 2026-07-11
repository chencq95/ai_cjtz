"""Database-driven scheduler that dispatches durable Celery crawl tasks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from croniter import croniter
from redis import Redis
from sqlalchemy import select

from .archive import archive_expired_snapshots
from .bootstrap import ensure_defaults
from .database import session_factory, session_scope
from .models import Alert, CrawlSchedule
from .tasks import dispatch_crawl, enrich_pending_reviews_task


logger = logging.getLogger(__name__)


def _next_run(expression: str, timezone_name: str, base: datetime | None = None) -> datetime:
    zone = ZoneInfo(timezone_name)
    current = (base or datetime.now(timezone.utc)).astimezone(zone)
    return croniter(expression, current).get_next(datetime).astimezone(timezone.utc)


def dispatch_due_schedules(settings: object) -> int:
    redis_client = Redis.from_url(str(getattr(settings, "redis_url", "redis://127.0.0.1:6379/0")))
    lock = redis_client.lock("dmp:scheduler:dispatch", timeout=max(int(getattr(settings, "scheduler_poll_seconds", 60)) - 2, 8), blocking_timeout=0)
    try:
        acquired = lock.acquire(blocking=False)
    except Exception:
        logger.exception("Redis scheduler lock unavailable")
        return 0
    if not acquired:
        return 0
    factory = session_factory(settings)
    now = datetime.now(timezone.utc)
    dispatched = 0
    try:
        with session_scope(factory) as session:
            due = session.scalars(
                select(CrawlSchedule).where(
                    CrawlSchedule.enabled.is_(True),
                    CrawlSchedule.next_run_at.is_not(None),
                    CrawlSchedule.next_run_at <= now,
                )
            ).all()
            for schedule in due:
                try:
                    import json

                    platform_ids = json.loads(schedule.platform_ids_json or "[]")
                    dispatch_crawl(
                        settings,
                        mode=schedule.mode,
                        platform_ids=platform_ids or None,
                        max_pages=schedule.max_pages,
                        requested_by=f"schedule:{schedule.id}",
                    )
                    schedule.last_run_at = now
                    schedule.next_run_at = _next_run(schedule.cron_expression, schedule.timezone, now)
                    dispatched += 1
                except Exception as exc:
                    session.add(
                        Alert(
                            severity="error",
                            alert_type="schedule_dispatch_failed",
                            title=f"计划任务分发失败：{schedule.name}",
                            message=str(exc),
                        )
                    )
                    logger.exception("Failed to dispatch schedule %s", schedule.id)
        return dispatched
    finally:
        try:
            lock.release()
        except Exception:
            pass


def build_scheduler(settings: object) -> BlockingScheduler:
    timezone_name = str(getattr(settings, "timezone", "Asia/Shanghai"))
    scheduler = BlockingScheduler(
        timezone=ZoneInfo(timezone_name),
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600},
    )
    scheduler.add_job(
        dispatch_due_schedules,
        IntervalTrigger(seconds=int(getattr(settings, "scheduler_poll_seconds", 60))),
        id="dispatch_due_schedules",
        kwargs={"settings": settings},
        replace_existing=True,
    )
    scheduler.add_job(
        archive_expired_snapshots,
        CronTrigger(hour=5, minute=0, timezone=ZoneInfo(timezone_name)),
        id="archive_expired_snapshots",
        kwargs={"settings": settings},
        replace_existing=True,
    )
    if bool(getattr(settings, "llm_enabled", False)):
        scheduler.add_job(
            enrich_pending_reviews_task.delay,
            IntervalTrigger(hours=1),
            id="enrich_pending_reviews",
            replace_existing=True,
        )
    return scheduler


def run_scheduler(settings: object, run_now: bool = False) -> None:
    logging.basicConfig(
        level=getattr(logging, str(getattr(settings, "log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    ensure_defaults(settings)
    if run_now:
        dispatch_crawl(settings, requested_by="scheduler_startup")
    scheduler = build_scheduler(settings)
    logger.info("Database scheduler started; timezone=%s", getattr(settings, "timezone", "Asia/Shanghai"))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        if scheduler.running:
            scheduler.shutdown(wait=False)
