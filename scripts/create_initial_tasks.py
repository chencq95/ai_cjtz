"""Create the first full and incremental crawl tasks exactly once.

The worker-level Redis lock guarantees that the incremental task waits until
the full task has finished, even when more than one Celery worker is running.
"""

from __future__ import annotations

import json

from sqlalchemy import select

from data_market_probe.database import session_factory, session_scope
from data_market_probe.models import CrawlTask
from data_market_probe.settings import get_settings
from data_market_probe.tasks import dispatch_crawl


ACTIVE = {"queued", "running"}


def _has_active(factory, mode: str) -> bool:
    with session_scope(factory) as session:
        return session.scalar(
            select(CrawlTask.id).where(CrawlTask.mode == mode, CrawlTask.status.in_(ACTIVE)).limit(1)
        ) is not None


def main() -> None:
    settings = get_settings()
    factory = session_factory(settings)
    created: list[dict[str, str]] = []
    if not _has_active(factory, "full"):
        full = dispatch_crawl(settings, mode="full", requested_by="admin")
        created.append({"mode": "full", "task_id": full.id})
    if not _has_active(factory, "incremental"):
        incremental = dispatch_crawl(settings, mode="incremental", requested_by="admin")
        created.append({"mode": "incremental", "task_id": incremental.id})
    print(json.dumps({"created": created}, ensure_ascii=False))


if __name__ == "__main__":
    main()
