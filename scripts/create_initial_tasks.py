"""Create the first full and incremental crawl tasks exactly once.

The worker-level Redis lock guarantees that the incremental task waits until
the full task has finished, even when more than one Celery worker is running.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from data_market_probe.database import session_factory, session_scope
from data_market_probe.models import CrawlSchedule, CrawlTask, Platform
from data_market_probe.settings import get_settings
from data_market_probe.tasks import dispatch_crawl
from data_market_probe.utils import json_dumps


ACTIVE = {"queued", "running"}


def _has_active(factory, mode: str) -> bool:
    with session_scope(factory) as session:
        return session.scalar(
            select(CrawlTask.id).where(CrawlTask.mode == mode, CrawlTask.status.in_(ACTIVE)).limit(1)
        ) is not None


def main() -> None:
    settings = get_settings()
    factory = session_factory(settings)
    with session_scope(factory) as session:
        platform_ids = list(
            session.scalars(
                select(Platform.id)
                .where(Platform.enabled.is_(True), Platform.onboarding_status == "active")
                .order_by(Platform.id)
            ).all()
        )
        for schedule in session.scalars(select(CrawlSchedule)).all():
            schedule.platform_ids_json = json_dumps(platform_ids)
    created: list[dict[str, str]] = []
    if not _has_active(factory, "full"):
        full = dispatch_crawl(settings, mode="full", platform_ids=platform_ids, requested_by="admin")
        created.append({"mode": "full", "task_id": full.id})
    if not _has_active(factory, "incremental"):
        incremental = dispatch_crawl(settings, mode="incremental", platform_ids=platform_ids, requested_by="admin")
        created.append({"mode": "incremental", "task_id": incremental.id})
    print(json.dumps({"platform_ids": platform_ids, "created": created}, ensure_ascii=False))


if __name__ == "__main__":
    main()
