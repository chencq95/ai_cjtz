from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from data_market_probe.crawler import _crawl_platform, _last_successful_api_page
from data_market_probe.database import create_db_engine
from data_market_probe.fetching import FetchResult
from data_market_probe.models import Base, CatalogItem, CrawlRun, Platform, PlatformRun, SourceCollection, UrlState


class _RateLimiter:
    def set_rate(self, _url: str, _rate: float) -> None:
        return None


class _PublicApiFetcher:
    rate_limiter = _RateLimiter()

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def fetch_api(self, url: str, **kwargs: object) -> FetchResult:
        self.calls.append({"url": url, **kwargs})
        body = json.dumps(
            {
                "code": 0,
                "data": {
                    "total": 2,
                    "records": [
                        {
                            "productId": "p-1",
                            "productName": "公开停车数据集",
                            "productIntroduction": "按月更新的停车资源公开目录",
                            "productTypeName": "数据集",
                            "industry": "交通运输",
                            "orgName": "示例提供方",
                        },
                        {
                            "productId": "p-2",
                            "productName": "公开物流数据集",
                            "productIntroduction": "公开物流资源目录",
                            "productTypeName": "数据集",
                            "industry": "交通运输",
                            "orgName": "示例提供方",
                        },
                    ],
                },
            },
            ensure_ascii=False,
        ).encode()
        return FetchResult(
            requested_url=url,
            final_url=url,
            status_code=200,
            headers={"content-type": "application/json"},
            body=body,
            mime_type="application/json",
            encoding="utf-8",
            method="http-api-post",
        )


class _Renderer:
    available = False


@pytest.mark.asyncio
async def test_public_api_adapter_paginates_persists_evidence_and_reconciles(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'api.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    rule = {
        "101": {
            "adapter": "public-api-v1",
            "collections": [
                {
                    "code": "data-products",
                    "entry_url": "https://example.com/public/products",
                    "request": {
                        "method": "POST",
                        "page_field": "pageNum",
                        "page_size_field": "pageSize",
                        "page_size": 2,
                        "payload": {"keyword": ""},
                        "records_path": "data.records",
                        "total_path": "data.total",
                        "success_field": "code",
                        "success_values": [0],
                        "detail_url_template": "https://example.com/products/{id}",
                    },
                }
            ],
        }
    }
    monkeypatch.setattr("data_market_probe.crawler._load_site_rules", lambda: rule)
    with factory() as session:
        session.add(
            Platform(
                id=101,
                name="Example exchange",
                source_url="https://example.com/",
                canonical_url="https://example.com/",
                enabled=True,
                adapter="public-api-v1",
                default_rate_limit=1.0,
            )
        )
        session.add(
            SourceCollection(
                platform_id=101,
                code="data-products",
                name="数据产品",
                object_kind="product",
                entry_url="https://example.com/public/products",
                enabled=True,
                pagination_mode="api",
            )
        )
        run = CrawlRun(mode="full", trigger="test")
        session.add(run)
        session.commit()
        run_id = run.id

    fetcher = _PublicApiFetcher()
    result = await _crawl_platform(
        settings=SimpleNamespace(
            raw_retention_days=365,
            classification_review_threshold=0.80,
            max_crawl_depth=1,
        ),
        factory=factory,
        run_id=run_id,
        platform_id=101,
        fetcher=fetcher,
        renderer=_Renderer(),
        object_store=None,
        fetch_semaphore=asyncio.Semaphore(1),
        full=True,
        max_pages=10,
        cancel_check=None,
    )

    assert result["status"] == "success"
    assert result["coverage"] == "complete"
    assert result["items_seen"] == 2
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0]["json_body"] == {"keyword": "", "pageNum": 1, "pageSize": 2}
    with factory() as session:
        platform_run = session.scalar(select(PlatformRun).where(PlatformRun.run_id == run_id))
        assert platform_run is not None
        assert platform_run.expected_count == 2
        assert platform_run.observed_count == 2
        assert json.loads(platform_run.completeness_json)["first_page_checks"][0]["record_count"] == 2
        assert session.get(Platform, 101).onboarding_status == "active"
        items = list(session.scalars(select(CatalogItem).where(CatalogItem.platform_id == 101)))
        assert {item.kind for item in items} == {"product"}
        assert {item.external_id for item in items} == {"p-1", "p-2"}

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_resumable_full_scan_uses_last_successful_api_page(db_session) -> None:
    collection = SourceCollection(
        platform_id=1,
        code="data-products",
        name="数据产品",
        object_kind="product",
        entry_url="https://example.com/public/products",
        coverage_status="partial",
    )
    db_session.add_all([Platform(id=1, name="测试交易所"), collection])
    db_session.flush()
    db_session.add_all(
        [
            UrlState(platform_id=1, collection_id=collection.id, canonical_url="https://example.com/public/products?_dmp_page=1", page_role="api", http_status=200),
            UrlState(platform_id=1, collection_id=collection.id, canonical_url="https://example.com/public/products?_dmp_page=1000", page_role="api", http_status=200),
            UrlState(platform_id=1, collection_id=collection.id, canonical_url="https://example.com/public/products?_dmp_page=1001", page_role="api", http_status=500),
        ]
    )
    db_session.flush()
    assert _last_successful_api_page(db_session, collection.id, collection.entry_url) == 1000
