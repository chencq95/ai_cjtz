"""Reconcile the 38 platform onboarding conclusions from durable crawl evidence.

This is intentionally idempotent and safe to run after a seed/import.  A
platform is ACTIVE once it has a usable, persisted public catalogue.  Strict
collection completeness remains available separately on PlatformRun and
SourceCollection and is never weakened by this reconciliation.
"""

from __future__ import annotations

from sqlalchemy import func, select

from data_market_probe.database import init_database, session_factory, session_scope
from data_market_probe.models import CatalogItem, Platform
from data_market_probe.settings import get_settings


def reconcile() -> dict[str, int]:
    settings = get_settings()
    init_database(settings)
    factory = session_factory(settings)
    counts: dict[str, int] = {}
    with session_scope(factory) as session:
        platforms = session.scalars(select(Platform).order_by(Platform.id)).all()
        for platform in platforms:
            if platform.source_role == "reference":
                conclusion = "out_of_scope"
            elif session.scalar(
                select(func.count(CatalogItem.id)).where(
                    CatalogItem.platform_id == platform.id,
                    CatalogItem.status == "active",
                )
            ):
                conclusion = "active"
            elif not platform.enabled or platform.url_status == "missing_url":
                conclusion = "blocked"
            elif platform.url_status in {"tls_cert_expired", "site_expired"}:
                conclusion = "offline"
            else:
                conclusion = "blocked"
            platform.onboarding_status = conclusion
            counts[conclusion] = counts.get(conclusion, 0) + 1
    return counts


if __name__ == "__main__":
    print(reconcile())
