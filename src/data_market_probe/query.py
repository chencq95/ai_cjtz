"""Read-only query service shared by the REST API and future MCP tools."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, desc, exists, func, or_, select
from sqlalchemy.orm import Session

from .models import (
    Alert,
    CatalogItem,
    CatalogItemVersion,
    ChangeEvent,
    CrawlError,
    CrawlRun,
    FieldEvidence,
    EntitySourceLink,
    ItemDimension,
    PageSnapshot,
    Platform,
    PlatformRun,
    SourceCollection,
    UrlState,
)
from .privacy import redact_sensitive, redact_text


def _json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def list_platforms(session: Session) -> list[dict[str, Any]]:
    platforms = session.scalars(select(Platform).order_by(Platform.id)).all()
    result: list[dict[str, Any]] = []
    for platform in platforms:
        latest = session.scalar(
            select(PlatformRun)
            .where(PlatformRun.platform_id == platform.id)
            .order_by(PlatformRun.started_at.desc())
            .limit(1)
        )
        item_count = session.scalar(
            select(func.count(CatalogItem.id)).where(
                CatalogItem.platform_id == platform.id,
                CatalogItem.status == "active",
            )
        ) or 0
        collections = session.scalar(
            select(func.count(SourceCollection.id)).where(SourceCollection.platform_id == platform.id)
        ) or 0
        result.append(
            {
                "id": platform.id,
                "province": platform.province,
                "city": platform.city,
                "name": platform.name or "国家数据局",
                "operator": platform.operator,
                "source_url": platform.source_url,
                "canonical_url": platform.canonical_url,
                "url_status": platform.url_status,
                "enabled": platform.enabled,
                "render_mode": platform.render_mode,
                "adapter": platform.adapter,
                "source_role": platform.source_role,
                "onboarding_status": platform.onboarding_status,
                "legal_review_status": platform.legal_review_status,
                "rate_limit": platform.default_rate_limit,
                "max_concurrency": platform.max_concurrency,
                "active_items": item_count,
                "collection_count": collections,
                "last_run": {
                    "status": latest.status,
                    "coverage": latest.coverage_status,
                    "started_at": _iso(latest.started_at),
                    "finished_at": _iso(latest.finished_at),
                    "pages": latest.pages_fetched,
                    "items": latest.items_seen,
                    "errors": latest.error_count,
                } if latest else None,
                "notes": platform.notes,
            }
        )
    return result


def list_collections(session: Session, platform_id: int | None = None) -> list[dict[str, Any]]:
    stmt = select(SourceCollection).order_by(SourceCollection.platform_id, SourceCollection.id)
    if platform_id is not None:
        stmt = stmt.where(SourceCollection.platform_id == platform_id)
    return [
        {
            "id": row.id,
            "platform_id": row.platform_id,
            "code": row.code,
            "name": row.name,
            "object_kind": row.object_kind,
            "entry_url": row.entry_url,
            "adapter": row.adapter,
            "adapter_version": row.adapter_version,
            "pagination_mode": row.pagination_mode,
            "expected_count": row.expected_count,
            "coverage_status": row.coverage_status,
            "last_complete_at": _iso(row.last_complete_at),
            "enabled": row.enabled,
            "notes": row.notes,
        }
        for row in session.scalars(stmt).all()
    ]


def search_catalog(
    session: Session,
    *,
    q: str | None = None,
    kind: str | None = None,
    product_type: str | None = None,
    industry: str | None = None,
    region: str | None = None,
    provider: str | None = None,
    platform_id: int | None = None,
    status: str = "active",
    published_from: datetime | None = None,
    published_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    current = CatalogItemVersion
    stmt = (
        select(CatalogItem, current, Platform, UrlState)
        .join(current, current.id == CatalogItem.current_version_id)
        .join(Platform, Platform.id == CatalogItem.platform_id)
        .outerjoin(UrlState, UrlState.id == CatalogItem.source_url_state_id)
    )
    filters: list[Any] = []
    if status:
        filters.append(CatalogItem.status == status)
    if q:
        pattern = f"%{q.strip()}%"
        filters.append(or_(CatalogItem.name.ilike(pattern), current.description.ilike(pattern), current.provider.ilike(pattern)))
    if kind:
        filters.append(CatalogItem.kind == kind)
    if product_type:
        filters.append(current.product_type == product_type)
    if provider:
        filters.append(current.provider.ilike(f"%{provider.strip()}%"))
    if platform_id is not None:
        filters.append(CatalogItem.platform_id == platform_id)
    if published_from:
        filters.append(current.published_at >= published_from)
    if published_to:
        filters.append(current.published_at <= published_to)
    if industry:
        filters.append(
            exists(select(ItemDimension.id).where(
                ItemDimension.version_id == current.id,
                ItemDimension.dimension_type == "industry",
                ItemDimension.normalized_value == industry,
            ))
        )
    if region:
        filters.append(
            exists(select(ItemDimension.id).where(
                ItemDimension.version_id == current.id,
                ItemDimension.dimension_type.in_(("platform_province", "platform_city", "coverage_region")),
                ItemDimension.normalized_value == region,
            ))
        )
    stmt = stmt.where(*filters)
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = session.scalar(count_stmt) or 0
    rows = session.execute(
        stmt.order_by(func.coalesce(current.published_at, current.detected_at).desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    items = []
    for item, version, platform, url_state in rows:
        dimensions = session.scalars(select(ItemDimension).where(ItemDimension.version_id == version.id)).all()
        items.append(
            {
                "id": item.id,
                "platform_id": platform.id,
                "platform_name": platform.name or "国家数据局",
                "kind": item.kind,
                "name": version.name,
                "description": redact_text(version.description),
                "provider": version.provider,
                "product_type": version.product_type,
                "product_type_raw": version.product_type_raw,
                "price": version.price_raw,
                "delivery_method": version.delivery_method,
                "refresh_frequency": version.refresh_frequency,
                "published_at": _iso(version.published_at),
                "source_updated_at": _iso(version.source_updated_at),
                "first_seen_at": _iso(item.first_seen_at),
                "last_seen_at": _iso(item.last_seen_at),
                "last_crawled_at": _iso(url_state.last_fetched_at if url_state else None),
                "source_url": version.source_url,
                "version": version.version_no,
                "confidence": version.extraction_confidence,
                "status": item.status,
                "dimensions": {dimension.dimension_type: dimension.normalized_value for dimension in dimensions},
                "data_as_of": _iso(version.detected_at),
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_item(session: Session, item_id: str) -> dict[str, Any] | None:
    item = session.get(CatalogItem, item_id)
    if item is None or item.current_version_id is None:
        return None
    version = session.get(CatalogItemVersion, item.current_version_id)
    platform = session.get(Platform, item.platform_id)
    state = session.get(UrlState, item.source_url_state_id) if item.source_url_state_id else None
    dimensions = session.scalars(select(ItemDimension).where(ItemDimension.version_id == version.id)).all()
    evidence = session.scalars(select(FieldEvidence).where(FieldEvidence.version_id == version.id)).all()
    snapshot = session.get(PageSnapshot, version.snapshot_id) if version.snapshot_id else None
    canonical_link = session.scalar(select(EntitySourceLink).where(EntitySourceLink.item_id == item.id))
    return {
        "id": item.id,
        "external_id": item.external_id,
        "canonical_entity_id": canonical_link.entity_id if canonical_link else None,
        "platform": {"id": platform.id, "name": platform.name or "国家数据局"},
        "kind": item.kind,
        "status": item.status,
        "name": version.name,
        "description": redact_text(version.description),
        "provider": version.provider,
        "product_type": version.product_type,
        "product_type_raw": version.product_type_raw,
        "price": version.price_raw,
        "delivery_method": version.delivery_method,
        "refresh_frequency": version.refresh_frequency,
        "published_at": _iso(version.published_at),
        "source_updated_at": _iso(version.source_updated_at),
        "data_period_start": _iso(version.data_period_start),
        "data_period_end": _iso(version.data_period_end),
        "source_url": version.source_url,
        "source_fields": redact_sensitive(_json(version.source_fields_json, {})),
        "normalized": _json(version.normalized_json, {}),
        "version": version.version_no,
        "confidence": version.extraction_confidence,
        "data_as_of": _iso(version.detected_at),
        "last_crawled_at": _iso(state.last_fetched_at if state else None),
        "dimensions": [
            {"type": row.dimension_type, "raw": row.raw_value, "value": row.normalized_value, "confidence": row.confidence, "method": row.method}
            for row in dimensions
        ],
        "evidence": [
            {"field": row.field_name, "raw_value": redact_text(row.raw_value), "locator": row.locator, "method": row.method, "confidence": row.confidence, "snapshot_id": row.snapshot_id}
            for row in evidence
        ],
        "snapshot": {
            "id": snapshot.id,
            "fetched_at": _iso(snapshot.fetched_at),
            "content_hash": snapshot.content_hash,
            "object_key": snapshot.object_key,
            "storage_tier": snapshot.storage_tier,
        } if snapshot else None,
    }


def item_versions(session: Session, item_id: str) -> list[dict[str, Any]]:
    versions = session.scalars(
        select(CatalogItemVersion).where(CatalogItemVersion.item_id == item_id).order_by(CatalogItemVersion.version_no.desc())
    ).all()
    return [
        {
            "id": row.id,
            "version": row.version_no,
            "name": row.name,
            "product_type": row.product_type,
            "provider": row.provider,
            "valid_from": _iso(row.valid_from),
            "valid_to": _iso(row.valid_to),
            "detected_at": _iso(row.detected_at),
            "semantic_hash": row.semantic_hash,
            "diff": _json(row.diff_json, {}),
            "source_url": row.source_url,
        }
        for row in versions
    ]


def catalog_facets(session: Session) -> dict[str, Any]:
    current_join = CatalogItemVersion.id == CatalogItem.current_version_id
    base = [CatalogItem.status == "active"]
    product_types = session.execute(
        select(CatalogItemVersion.product_type, func.count(CatalogItem.id))
        .join(CatalogItem, current_join)
        .where(*base)
        .group_by(CatalogItemVersion.product_type)
        .order_by(func.count(CatalogItem.id).desc())
    ).all()
    platforms = session.execute(
        select(Platform.id, Platform.name, func.count(CatalogItem.id))
        .join(CatalogItem, CatalogItem.platform_id == Platform.id)
        .where(*base)
        .group_by(Platform.id, Platform.name)
        .order_by(func.count(CatalogItem.id).desc())
    ).all()
    dimensions = session.execute(
        select(ItemDimension.dimension_type, ItemDimension.normalized_value, func.count(func.distinct(ItemDimension.item_id)))
        .join(CatalogItem, CatalogItem.id == ItemDimension.item_id)
        .where(CatalogItem.status == "active", ItemDimension.dimension_type.in_(("industry", "platform_province", "coverage_region")))
        .group_by(ItemDimension.dimension_type, ItemDimension.normalized_value)
        .order_by(func.count(func.distinct(ItemDimension.item_id)).desc())
    ).all()
    providers = session.execute(
        select(CatalogItemVersion.provider, func.count(CatalogItem.id))
        .join(CatalogItem, current_join)
        .where(*base, CatalogItemVersion.provider != "")
        .group_by(CatalogItemVersion.provider)
        .order_by(func.count(CatalogItem.id).desc())
        .limit(100)
    ).all()
    return {
        "product_types": [{"value": value, "count": count} for value, count in product_types],
        "platforms": [{"id": pid, "value": name or "国家数据局", "count": count} for pid, name, count in platforms],
        "dimensions": [{"type": dtype, "value": value, "count": count} for dtype, value, count in dimensions],
        "providers": [{"value": value, "count": count} for value, count in providers],
    }


def dashboard(session: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    latest_run = session.scalar(select(CrawlRun).order_by(CrawlRun.started_at.desc()).limit(1))
    return {
        "platforms": session.scalar(select(func.count(Platform.id))) or 0,
        "active_platforms": session.scalar(select(func.count(Platform.id)).where(Platform.enabled.is_(True))) or 0,
        "active_items": session.scalar(select(func.count(CatalogItem.id)).where(CatalogItem.status == "active")) or 0,
        "open_alerts": session.scalar(select(func.count(Alert.id)).where(Alert.status == "open")) or 0,
        "items_added_24h": session.scalar(select(func.count(ChangeEvent.id)).where(ChangeEvent.event_type == "added", ChangeEvent.created_at >= now - timedelta(days=1))) or 0,
        "items_updated_24h": session.scalar(select(func.count(ChangeEvent.id)).where(ChangeEvent.event_type == "updated", ChangeEvent.created_at >= now - timedelta(days=1))) or 0,
        "latest_run": {
            "id": latest_run.id,
            "status": latest_run.status,
            "mode": latest_run.mode,
            "started_at": _iso(latest_run.started_at),
            "finished_at": _iso(latest_run.finished_at),
            "stats": _json(latest_run.stats_json, {}),
        } if latest_run else None,
    }


def coverage_matrix(session: Session) -> list[dict[str, Any]]:
    result = []
    for platform in session.scalars(select(Platform).order_by(Platform.id)).all():
        collections = session.scalars(select(SourceCollection).where(SourceCollection.platform_id == platform.id).order_by(SourceCollection.id)).all()
        collection_rows = []
        for row in collections:
            discovered = session.scalar(
                select(func.count(CatalogItem.id)).where(CatalogItem.collection_id == row.id)
            ) or 0
            active = session.scalar(
                select(func.count(CatalogItem.id)).where(
                    CatalogItem.collection_id == row.id,
                    CatalogItem.status == "active",
                )
            ) or 0
            detail_success = session.scalar(
                select(func.count(CatalogItem.id)).where(
                    CatalogItem.collection_id == row.id,
                    CatalogItem.current_version_id.is_not(None),
                )
            ) or 0
            versions = session.scalar(
                select(func.count(CatalogItemVersion.id))
                .join(CatalogItem, CatalogItem.id == CatalogItemVersion.item_id)
                .where(CatalogItem.collection_id == row.id)
            ) or 0
            errors = session.scalar(
                select(func.count(CrawlError.id)).where(CrawlError.collection_id == row.id)
            ) or 0
            reconciliation = (active / row.expected_count) if row.expected_count else None
            detail_rate = (detail_success / discovered) if discovered else None
            collection_rows.append(
                {
                    "id": row.id,
                    "name": row.name,
                    "kind": row.object_kind,
                    "status": row.coverage_status,
                    "expected": row.expected_count,
                    "discovered": discovered,
                    "active": active,
                    "detail_success": detail_success,
                    "detail_rate": detail_rate,
                    "reconciliation_rate": reconciliation,
                    "version_count": versions,
                    "error_count": errors,
                    "adapter": row.adapter,
                    "adapter_version": row.adapter_version,
                    "entry_url": row.entry_url,
                    "last_complete_at": _iso(row.last_complete_at),
                    "enabled": row.enabled,
                    "notes": row.notes,
                }
            )
        terminal = {entry["status"] for entry in collection_rows if entry["enabled"]}
        conclusion = platform.onboarding_status.upper()
        if terminal and terminal <= {"complete"}:
            conclusion = "COMPLETE"
        elif "blocked" in terminal:
            conclusion = "BLOCKED"
        elif "offline" in terminal:
            conclusion = "OFFLINE"
        result.append({
            "platform_id": platform.id,
            "platform_name": platform.name or "国家数据局",
            "onboarding_status": platform.onboarding_status,
            "conclusion": conclusion,
            "source_url": platform.canonical_url or platform.source_url,
            "collections": collection_rows,
        })
    return result
