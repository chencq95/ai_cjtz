"""Reconcile the 38 platform onboarding conclusions from durable crawl evidence.

This is intentionally idempotent and safe to run after a seed/import.  It never
turns a partial run into COMPLETE; only the latest full PlatformRun with a
complete coverage status can do that.
"""

from __future__ import annotations

from sqlalchemy import select

from data_market_probe.database import init_database, session_factory, session_scope
from data_market_probe.models import CrawlRun, Platform, PlatformRun
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
            elif not platform.enabled or platform.url_status == "missing_url":
                conclusion = "blocked"
            elif platform.url_status in {"tls_cert_expired", "site_expired"}:
                conclusion = "offline"
            else:
                latest = session.scalar(
                    select(PlatformRun)
                    .join(CrawlRun, CrawlRun.id == PlatformRun.run_id)
                    .where(
                        PlatformRun.platform_id == platform.id,
                        CrawlRun.mode == "full",
                    )
                    .order_by(CrawlRun.finished_at.desc())
                )
                conclusion = "complete" if latest and latest.coverage_status == "complete" else "blocked"
            platform.onboarding_status = conclusion
            counts[conclusion] = counts.get(conclusion, 0) + 1
    return counts


if __name__ == "__main__":
    print(reconcile())
