"""CLI status summary."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from .database import session_factory, session_scope
from .models import Alert, CatalogItem, CrawlRun, Platform


def get_status(settings: object) -> dict[str, object]:
    factory = session_factory(settings)
    with session_scope(factory) as session:
        latest = session.scalar(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(1))
        return {
            "database_url": str(getattr(settings, "database_url", "")),
            "platforms": session.scalar(select(func.count(Platform.id))) or 0,
            "active_items": session.scalar(select(func.count(CatalogItem.id)).where(CatalogItem.status == "active")) or 0,
            "open_alerts": session.scalar(select(func.count(Alert.id)).where(Alert.status == "open")) or 0,
            "latest_run": {
                "id": latest.id,
                "status": latest.status,
                "mode": latest.mode,
                "started_at": latest.started_at,
                "finished_at": latest.finished_at,
                "stats": json.loads(latest.stats_json or "{}"),
            } if latest else None,
        }

