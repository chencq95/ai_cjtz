"""Celery task dispatch and durable task-state tracking."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from celery import Celery
from sqlalchemy import select

from .crawler import run_crawl
from .database import init_database, session_factory, session_scope
from .enrichment import enrich_pending_reviews
from .models import CrawlTask, RunLog
from .settings import get_settings
from .utils import json_dumps


settings = get_settings()
celery_app = Celery("data_market_probe", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_time_limit=60 * 60 * 12,
    task_soft_time_limit=60 * 60 * 11,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.celery_eager,
)


@celery_app.task(name="data_market_probe.enrich_pending_reviews")
def enrich_pending_reviews_task() -> dict[str, int | str]:
    return enrich_pending_reviews(get_settings())


def _log(session: Any, task_id: str, message: str, level: str = "INFO", run_id: str | None = None) -> None:
    session.add(RunLog(task_id=task_id, run_id=run_id, level=level, message=message))


@celery_app.task(bind=True, name="data_market_probe.execute_crawl")
def execute_crawl(self: Any, task_id: str) -> dict[str, Any]:
    current_settings = get_settings()
    init_database(current_settings)
    factory = session_factory(current_settings)
    with session_scope(factory) as session:
        record = session.get(CrawlTask, task_id)
        if record is None:
            raise ValueError(f"crawl task not found: {task_id}")
        if record.cancel_requested:
            record.status = "cancelled"
            record.finished_at = datetime.now(timezone.utc)
            _log(session, task_id, "任务在启动前被取消", "WARNING")
            return {"task_id": task_id, "status": "cancelled"}
        record.celery_task_id = self.request.id or record.celery_task_id
        record.status = "running"
        record.started_at = datetime.now(timezone.utc)
        platform_ids = json.loads(record.platform_ids_json or "[]")
        mode = record.mode
        _log(session, task_id, f"开始{mode}采集，平台范围：{platform_ids or '全部'}")
    try:
        def is_cancelled() -> bool:
            with session_scope(factory) as check_session:
                current = check_session.get(CrawlTask, task_id)
                return bool(current and current.cancel_requested)

        result = asyncio.run(
            run_crawl(
                current_settings,
                platform_ids=platform_ids or None,
                full=mode == "full",
                trigger="queue",
                max_pages=record.max_pages,
                cancel_check=is_cancelled,
            )
        )
        with session_scope(factory) as session:
            record = session.get(CrawlTask, task_id)
            if record is not None:
                record.run_id = result.get("run_id")
                if record.cancel_requested or result.get("status") == "cancelled":
                    record.status = "cancelled"
                else:
                    record.status = "success" if result.get("status") == "success" else "partial"
                record.result_json = json_dumps(result)
                record.finished_at = datetime.now(timezone.utc)
                _log(session, task_id, f"采集完成：{result.get('status')}", run_id=record.run_id)
        return result
    except Exception as exc:
        with session_scope(factory) as session:
            record = session.get(CrawlTask, task_id)
            if record is not None:
                record.status = "failed"
                record.error_message = str(exc)[:8000]
                record.finished_at = datetime.now(timezone.utc)
                _log(session, task_id, f"采集失败：{exc}", "ERROR", record.run_id)
        raise


def dispatch_crawl(
    settings_obj: object,
    *,
    mode: str = "incremental",
    platform_ids: list[int] | None = None,
    max_pages: int | None = None,
    requested_by: str = "system",
) -> CrawlTask:
    init_database(settings_obj)
    factory = session_factory(settings_obj)
    with session_scope(factory) as session:
        record = CrawlTask(
            mode=mode,
            platform_ids_json=json_dumps(platform_ids or []),
            max_pages=max_pages,
            requested_by=requested_by,
        )
        session.add(record)
        session.flush()
        task_id = record.id
        _log(session, task_id, "任务已进入队列")
    async_result = execute_crawl.delay(task_id)
    with session_scope(factory) as session:
        record = session.get(CrawlTask, task_id)
        if record is not None:
            record.celery_task_id = async_result.id or ""
            session.expunge(record)
            return record
    raise RuntimeError("failed to persist crawl task")


def request_cancel(settings_obj: object, task_id: str) -> bool:
    factory = session_factory(settings_obj)
    celery_task_id = ""
    with session_scope(factory) as session:
        record = session.get(CrawlTask, task_id)
        if record is None or record.status in {"success", "partial", "failed", "cancelled"}:
            return False
        record.cancel_requested = True
        celery_task_id = record.celery_task_id
        _log(session, task_id, "管理员请求取消任务", "WARNING", record.run_id)
    if celery_task_id:
        celery_app.control.revoke(celery_task_id, terminate=False)
    return True
