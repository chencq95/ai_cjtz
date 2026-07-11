from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from data_market_probe.extraction import Dimension, Evidence, ExtractedItem
from data_market_probe.models import (
    CatalogItem,
    CatalogItemVersion,
    EntitySourceLink,
    ItemDimension,
    ChangeEvent,
    CrawlRun,
    Platform,
    TaxonomyMapping,
    UrlState,
    utcnow,
)
from data_market_probe.repository import CatalogRepository


def _item(description: str) -> ExtractedItem:
    return ExtractedItem(
        kind="product",
        name="企业风险查询",
        source_url="https://example.com/product/1",
        external_id="product-1",
        description=description,
        provider="示例公司",
        product_type_raw="数据接口",
        product_type="api",
        confidence=0.95,
        dimensions=[Dimension("industry", "金融", "finance", 0.95, "source")],
        evidence=[Evidence("name", "企业风险查询", "json.productName")],
    )


def test_semantically_identical_item_does_not_create_duplicate_version(db_session) -> None:
    platform = Platform(id=1, name="测试交易所")
    run = CrawlRun(mode="incremental")
    state = UrlState(platform_id=1, canonical_url="https://example.com/product/1")
    db_session.add_all([platform, run, state])
    db_session.flush()
    repository = CatalogRepository(db_session)

    first, event = repository.upsert_item(platform=platform, collection_id=None, source_state=state, run=run, snapshot=None, extracted=_item("第一版简介"))
    assert event == "added"
    second, event = repository.upsert_item(platform=platform, collection_id=None, source_state=state, run=run, snapshot=None, extracted=_item("第一版简介"))
    assert first.id == second.id
    assert event == "unchanged"
    assert db_session.scalar(select(func.count(CatalogItemVersion.id))) == 1

    repository.upsert_item(platform=platform, collection_id=None, source_state=state, run=run, snapshot=None, extracted=_item("第二版简介"))
    assert db_session.scalar(select(func.count(CatalogItemVersion.id))) == 2
    versions = db_session.scalars(select(CatalogItemVersion).order_by(CatalogItemVersion.version_no)).all()
    assert versions[0].valid_to is not None
    assert versions[1].version_no == 2


def test_removal_requires_three_complete_full_scans(db_session) -> None:
    platform = Platform(id=1, name="测试交易所")
    initial_run = CrawlRun(mode="incremental")
    state = UrlState(platform_id=1, canonical_url="https://example.com/product/1")
    db_session.add_all([platform, initial_run, state])
    db_session.flush()
    repository = CatalogRepository(db_session)
    item, _ = repository.upsert_item(platform=platform, collection_id=None, source_state=state, run=initial_run, snapshot=None, extracted=_item("简介"))
    db_session.flush()

    partial = CrawlRun(mode="full", started_at=utcnow())
    db_session.add(partial)
    db_session.flush()
    item.last_seen_at = partial.started_at - timedelta(days=1)
    assert repository.reconcile_missing_items(run=partial, platform=platform, coverage_complete=False) == 0
    assert item.missing_full_scans == 0

    for expected in (1, 2, 3):
        run = CrawlRun(mode="full", started_at=utcnow())
        db_session.add(run)
        db_session.flush()
        item.last_seen_at = run.started_at - timedelta(days=1)
        removed = repository.reconcile_missing_items(run=run, platform=platform, coverage_complete=True)
        assert item.missing_full_scans == expected
        assert removed == (1 if expected == 3 else 0)
    assert item.status == "inactive"
    assert db_session.scalar(select(func.count(ChangeEvent.id)).where(ChangeEvent.event_type == "removed")) == 1


def test_taxonomy_mapping_is_applied_before_version_hash(db_session) -> None:
    platform = Platform(id=1, name="测试交易所")
    run = CrawlRun(mode="incremental")
    state = UrlState(platform_id=1, canonical_url="https://example.com/product/1")
    mapping = TaxonomyMapping(
        dimension_type="product_type",
        raw_value="数据接口",
        normalized_value="data_service",
        confidence=1.0,
    )
    db_session.add_all([platform, run, state, mapping])
    db_session.flush()
    extracted = _item("简介")
    extracted.dimensions.append(Dimension("product_type", "数据接口", "api", 0.6, "rule"))

    CatalogRepository(db_session).upsert_item(
        platform=platform,
        collection_id=None,
        source_state=state,
        run=run,
        snapshot=None,
        extracted=extracted,
    )
    version = db_session.scalar(select(CatalogItemVersion))
    dimension = db_session.scalar(
        select(ItemDimension).where(ItemDimension.dimension_type == "product_type")
    )
    assert version.product_type == "data_service"
    assert dimension.normalized_value == "data_service"
    assert dimension.method == "taxonomy_mapping"


def test_same_name_and_provider_link_across_platforms(db_session) -> None:
    platforms = [Platform(id=1, name="交易所甲"), Platform(id=2, name="交易所乙")]
    run = CrawlRun(mode="incremental")
    states = [
        UrlState(platform_id=1, canonical_url="https://one.example/product/1"),
        UrlState(platform_id=2, canonical_url="https://two.example/product/9"),
    ]
    db_session.add_all([*platforms, run, *states])
    db_session.flush()
    repository = CatalogRepository(db_session)
    for index in range(2):
        extracted = _item("相同简介")
        extracted.external_id = f"source-{index}"
        extracted.source_url = states[index].canonical_url
        repository.upsert_item(
            platform=platforms[index], collection_id=None, source_state=states[index],
            run=run, snapshot=None, extracted=extracted,
        )
    links = db_session.scalars(select(EntitySourceLink).order_by(EntitySourceLink.id)).all()
    assert len(links) == 2
    assert links[0].entity_id == links[1].entity_id
    assert links[1].match_method == "exact_name_provider"
