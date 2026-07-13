from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from data_market_probe.crawler import _crawl_platform
from data_market_probe.database import create_db_engine
from data_market_probe.fetching import FetchResult
from data_market_probe.models import Base, CrawlRun, Platform, PlatformRun


class _RateLimiter:
    def set_rate(self, _url: str, _rate: float) -> None:
        return None


class _NotModifiedFetcher:
    rate_limiter = _RateLimiter()

    async def fetch(self, url: str, **_kwargs: object) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=304,
            headers={"etag": '"same"'},
            body=b"",
            mime_type="text/html",
            encoding="utf-8",
        )


class _Renderer:
    available = False


@pytest.mark.asyncio
async def test_not_modified_response_counts_as_a_successful_fetch(tmp_path) -> None:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'crawl.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        session.add(
            Platform(
                id=1,
                name="Example exchange",
                source_url="https://example.com/",
                canonical_url="https://example.com/",
                enabled=True,
                default_rate_limit=1.0,
            )
        )
        run = CrawlRun(mode="incremental", trigger="test")
        session.add(run)
        session.commit()
        run_id = run.id

    result = await _crawl_platform(
        settings=SimpleNamespace(
            raw_retention_days=365,
            classification_review_threshold=0.80,
            max_crawl_depth=1,
        ),
        factory=factory,
        run_id=run_id,
        platform_id=1,
        fetcher=_NotModifiedFetcher(),
        renderer=_Renderer(),
        object_store=None,
        fetch_semaphore=asyncio.Semaphore(1),
        full=False,
        max_pages=1,
        cancel_check=None,
    )

    assert result["pages"] == 1
    assert result["status"] == "partial"  # The one-page test cap left sitemap URLs queued.
    with factory() as session:
        platform_run = session.scalar(select(PlatformRun).where(PlatformRun.run_id == run_id))
        assert platform_run is not None
        assert platform_run.pages_fetched == 1
        assert platform_run.error_count == 0

    Base.metadata.drop_all(engine)
    engine.dispose()
