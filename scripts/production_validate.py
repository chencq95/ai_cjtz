"""Production smoke checks and idempotent first full-crawl dispatch.

The script intentionally prints only non-secret validation data.  It is run
inside the API image after a deployment, where the production settings are
already supplied through environment variables.
"""

from __future__ import annotations

import argparse
import json
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from data_market_probe.api import create_app
from data_market_probe.auth import hash_password
from data_market_probe.database import session_factory, session_scope
from data_market_probe.models import (
    CatalogItem,
    CatalogItemVersion,
    CrawlRun,
    CrawlSchedule,
    CrawlTask,
    FieldEvidence,
    Platform,
    SourceCollection,
    User,
)
from data_market_probe.settings import get_settings


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    _check(response.status_code == 200, f"login failed: HTTP {response.status_code}")


def validate(*, enqueue_full_if_empty: bool) -> dict[str, Any]:
    settings = get_settings()
    factory = session_factory(settings)
    readonly_username = f"smoke-readonly-{secrets.token_hex(5)}"
    readonly_password = secrets.token_urlsafe(24)

    with session_scope(factory) as session:
        platform_count = session.scalar(select(func.count(Platform.id))) or 0
        collection_count = session.scalar(select(func.count(SourceCollection.id))) or 0
        enabled_collection_count = session.scalar(
            select(func.count(SourceCollection.id)).where(SourceCollection.enabled.is_(True))
        ) or 0
        schedules = session.scalars(select(CrawlSchedule).order_by(CrawlSchedule.id)).all()
        schedule_contracts = {
            (row.cron_expression, row.timezone, row.mode) for row in schedules if row.enabled
        }
        user = User(
            username=readonly_username,
            password_hash=hash_password(readonly_password),
            role="readonly",
            enabled=True,
            must_change_password=False,
        )
        session.add(user)

    app = create_app()
    try:
        with TestClient(app) as client:
            health = client.get("/api/health")
            _check(health.status_code == 200 and health.json().get("status") == "ok", "health failed")

            _login(client, settings.bootstrap_admin_username, settings.bootstrap_admin_password)
            me = client.get("/api/v1/auth/me")
            _check(me.status_code == 200 and me.json().get("role") == "admin", "admin role failed")
            platforms = client.get("/api/v1/platforms")
            coverage = client.get("/api/v1/coverage")
            schedule_response = client.get("/api/v1/schedules")
            _check(platforms.status_code == 200 and len(platforms.json()) == 38, "platform API count failed")
            _check(coverage.status_code == 200 and len(coverage.json()) == 38, "coverage API count failed")
            _check(schedule_response.status_code == 200, "schedule API failed")

        with TestClient(app) as readonly_client:
            _login(readonly_client, readonly_username, readonly_password)
            denied = readonly_client.post("/api/v1/tasks", json={"mode": "incremental"})
            _check(denied.status_code == 403, "readonly user could trigger a crawl")
    finally:
        with session_scope(factory) as session:
            smoke_user = session.scalar(select(User).where(User.username == readonly_username))
            if smoke_user is not None:
                session.delete(smoke_user)

    _check(platform_count == 38, f"expected 38 platforms, got {platform_count}")
    _check(("30 2 * * *", "Asia/Shanghai", "incremental") in schedule_contracts, "daily schedule missing")
    _check(("30 4 * * 0", "Asia/Shanghai", "full") in schedule_contracts, "weekly schedule missing")

    dispatched_task_id: list[str] = []
    with session_scope(factory) as session:
        task_count = session.scalar(select(func.count(CrawlTask.id))) or 0
        existing_modes = set(session.scalars(select(CrawlTask.mode).where(CrawlTask.status.in_(("queued", "running")))).all())

    if enqueue_full_if_empty and "full" not in existing_modes:
        from data_market_probe.tasks import dispatch_crawl

        full_task = dispatch_crawl(settings, mode="full", requested_by="admin")
        dispatched_task_id.append(full_task.id)
    if enqueue_full_if_empty and "incremental" not in existing_modes:
        from data_market_probe.tasks import dispatch_crawl

        incremental_task = dispatch_crawl(settings, mode="incremental", requested_by="admin")
        dispatched_task_id.append(incremental_task.id)

    with session_scope(factory) as session:
        task_count = session.scalar(select(func.count(CrawlTask.id))) or 0
        run_count = session.scalar(select(func.count(CrawlRun.id))) or 0
        item_count = session.scalar(select(func.count(CatalogItem.id))) or 0
        version_count = session.scalar(select(func.count(CatalogItemVersion.id))) or 0
        evidence_count = session.scalar(select(func.count(FieldEvidence.id))) or 0
        latest_task = session.scalar(select(CrawlTask).order_by(CrawlTask.created_at.desc()).limit(1))
        onboarding = dict(
            session.execute(
                select(Platform.onboarding_status, func.count(Platform.id)).group_by(Platform.onboarding_status)
            ).all()
        )

    return {
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "health": True,
            "admin_login": True,
            "readonly_denied_management": True,
            "platform_api_count": 38,
            "coverage_api_count": 38,
            "daily_incremental_schedule": True,
            "weekly_full_schedule": True,
        },
        "database": {
            "platforms": platform_count,
            "collections": collection_count,
            "enabled_collections": enabled_collection_count,
            "onboarding_statuses": onboarding,
            "tasks": task_count,
            "runs": run_count,
            "items": item_count,
            "versions": version_count,
            "field_evidence": evidence_count,
        },
        "crawl": {
            "dispatched_task_id": dispatched_task_id,
            "latest_task_id": latest_task.id if latest_task else None,
            "latest_task_status": latest_task.status if latest_task else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enqueue-full-if-empty", action="store_true")
    args = parser.parse_args()
    report = validate(enqueue_full_if_empty=args.enqueue_full_if_empty)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
