"""Idempotent first-run initialization for data, users and schedules."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import func, select

from .auth import hash_password
from .database import init_database, session_factory, session_scope
from .models import CrawlSchedule, TaxonomyMapping, User
from .seed import seed_platforms


DEFAULT_MAPPINGS = [
    ("product_type", "数据集", "dataset"),
    ("product_type", "数据接口", "api"),
    ("product_type", "API", "api"),
    ("product_type", "数据报告", "report"),
    ("product_type", "数据服务", "data_service"),
    ("object_kind", "数据产品", "product"),
    ("object_kind", "数据组件", "component"),
    ("object_kind", "应用场景", "scenario"),
]


def _next(cron_expression: str, timezone_name: str) -> datetime:
    local_now = datetime.now(ZoneInfo(timezone_name))
    return croniter(cron_expression, local_now).get_next(datetime).astimezone(timezone.utc)


def ensure_defaults(settings: object) -> dict[str, int]:
    init_database(settings)
    seed_result = seed_platforms(settings)
    factory = session_factory(settings)
    users = schedules = mappings = 0
    with session_scope(factory) as session:
        username = str(getattr(settings, "bootstrap_admin_username", "admin"))
        if session.scalar(select(User).where(User.username == username)) is None:
            session.add(
                User(
                    username=username,
                    password_hash=hash_password(str(getattr(settings, "bootstrap_admin_password", "ChangeMe123!"))),
                    role="admin",
                    must_change_password=True,
                )
            )
            users += 1
        defaults = (
            ("每日增量采集", "30 2 * * *", "incremental"),
            ("每周完整校准", "30 4 * * 0", "full"),
        )
        for name, cron_expression, mode in defaults:
            if session.scalar(select(CrawlSchedule).where(CrawlSchedule.name == name)) is None:
                session.add(
                    CrawlSchedule(
                        name=name,
                        cron_expression=cron_expression,
                        timezone=str(getattr(settings, "timezone", "Asia/Shanghai")),
                        mode=mode,
                        next_run_at=_next(cron_expression, str(getattr(settings, "timezone", "Asia/Shanghai"))),
                    )
                )
                schedules += 1
        for dimension_type, raw_value, normalized_value in DEFAULT_MAPPINGS:
            exists = session.scalar(
                select(TaxonomyMapping).where(
                    TaxonomyMapping.dimension_type == dimension_type,
                    TaxonomyMapping.raw_value == raw_value,
                )
            )
            if exists is None:
                session.add(
                    TaxonomyMapping(
                        dimension_type=dimension_type,
                        raw_value=raw_value,
                        normalized_value=normalized_value,
                    )
                )
                mappings += 1
    return {"platforms": int(seed_result["rows"]), "users": users, "schedules": schedules, "mappings": mappings}
