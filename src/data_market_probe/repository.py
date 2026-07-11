"""Transactional persistence and idempotent versioning operations."""

from __future__ import annotations

import gzip
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .extraction import ExtractedItem, ExtractedPage
from .models import (
    CatalogItem,
    CatalogItemVersion,
    CanonicalEntity,
    ChangeEvent,
    ClassificationReview,
    CrawlError,
    CrawlRun,
    FieldEvidence,
    EntitySourceLink,
    ItemDimension,
    PageLink,
    PageSnapshot,
    Platform,
    PlatformRun,
    TaxonomyMapping,
    UrlState,
    utcnow,
)
from .object_store import ObjectStore
from .utils import canonicalize_url, json_dumps, normalize_text, semantic_text_hash, sha256_bytes, sha256_text


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _natural_key(item: ExtractedItem) -> str:
    if item.external_id:
        raw = f"external:{item.kind}:{item.external_id.strip().lower()}"
    elif item.source_url:
        raw = f"url:{item.kind}:{canonicalize_url(item.source_url)}"
    else:
        raw = f"name:{item.kind}:{item.name.strip().lower()}:{item.provider.strip().lower()}"
    return sha256_text(raw)


def _semantic_payload(item: ExtractedItem) -> dict[str, Any]:
    return {
        "kind": item.kind,
        "name": item.name,
        "external_id": item.external_id,
        "description": item.description,
        "provider": item.provider,
        "product_type_raw": item.product_type_raw,
        "product_type": item.product_type,
        "price_raw": item.price_raw,
        "delivery_method": item.delivery_method,
        "refresh_frequency": item.refresh_frequency,
        "data_period_start": item.data_period_start.isoformat() if item.data_period_start else None,
        "data_period_end": item.data_period_end.isoformat() if item.data_period_end else None,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "source_updated_at": item.source_updated_at.isoformat() if item.source_updated_at else None,
        "source_url": canonicalize_url(item.source_url),
        "source_fields": item.source_fields,
        "dimensions": sorted(
            (
                dimension.dimension_type,
                dimension.normalized_value,
                dimension.raw_value,
            )
            for dimension in item.dimensions
        ),
    }


def _version_payload(version: CatalogItemVersion | None) -> dict[str, Any]:
    if version is None:
        return {}
    return {
        "kind": None,
        "name": version.name,
        "description": version.description,
        "provider": version.provider,
        "product_type_raw": version.product_type_raw,
        "product_type": version.product_type,
        "price_raw": version.price_raw,
        "delivery_method": version.delivery_method,
        "refresh_frequency": version.refresh_frequency,
        "data_period_start": version.data_period_start.isoformat() if version.data_period_start else None,
        "data_period_end": version.data_period_end.isoformat() if version.data_period_end else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
        "source_updated_at": version.source_updated_at.isoformat() if version.source_updated_at else None,
        "source_url": version.source_url,
        "source_fields": json.loads(version.source_fields_json or "{}"),
    }


def _field_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            result[key] = {"old": old.get(key), "new": new.get(key)}
    return result


class CatalogRepository:
    def __init__(
        self,
        session: Session,
        object_store: ObjectStore | None = None,
        raw_retention_days: int = 365,
        review_threshold: float = 0.80,
    ):
        self.session = session
        self.object_store = object_store
        self.raw_retention_days = raw_retention_days
        self.review_threshold = review_threshold

    def create_run(self, mode: str, trigger: str, config: dict[str, Any]) -> CrawlRun:
        run = CrawlRun(mode=mode, trigger=trigger, config_json=json_dumps(config))
        self.session.add(run)
        self.session.flush()
        return run

    def start_platform_run(self, run: CrawlRun, platform_id: int) -> PlatformRun:
        platform_run = PlatformRun(run_id=run.id, platform_id=platform_id)
        self.session.add(platform_run)
        self.session.flush()
        return platform_run

    def get_or_create_url(
        self,
        *,
        platform_id: int,
        url: str,
        collection_id: int | None = None,
        discovered_from: str = "",
        anchor_text: str = "",
        depth: int = 0,
        page_role: str = "unknown",
    ) -> UrlState:
        canonical = canonicalize_url(url)
        if not canonical:
            raise ValueError(f"not a canonical HTTP URL: {url}")
        state = self.session.scalar(
            select(UrlState).where(
                UrlState.platform_id == platform_id,
                UrlState.canonical_url == canonical,
            )
        )
        now = _now()
        if state is None:
            state = UrlState(
                platform_id=platform_id,
                collection_id=collection_id,
                canonical_url=canonical,
                discovered_from=discovered_from,
                anchor_text=anchor_text,
                depth=depth,
                page_role=page_role,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(state)
            self.session.flush()
        else:
            state.last_seen_at = now
            state.active = True
            if collection_id and not state.collection_id:
                state.collection_id = collection_id
            if depth < state.depth:
                state.depth = depth
            if anchor_text and not state.anchor_text:
                state.anchor_text = anchor_text
            if page_role != "unknown" and state.page_role == "unknown":
                state.page_role = page_role
        return state

    def mark_not_modified(self, state: UrlState, status_code: int, headers: dict[str, str]) -> None:
        state.http_status = status_code
        state.last_fetched_at = _now()
        state.consecutive_errors = 0
        state.etag = headers.get("etag", state.etag)
        state.last_modified = headers.get("last-modified", state.last_modified)

    def save_snapshot(
        self,
        *,
        state: UrlState,
        run: CrawlRun,
        status_code: int,
        final_url: str,
        mime_type: str,
        encoding: str,
        headers: dict[str, str],
        raw_body: bytes,
        extracted: ExtractedPage,
        fetch_method: str,
        truncated: bool = False,
    ) -> tuple[PageSnapshot | None, bool]:
        content_hash = sha256_bytes(raw_body)
        semantic_hash = semantic_text_hash(extracted.text)
        changed = content_hash != state.content_hash or semantic_hash != state.semantic_hash
        now = _now()
        state.last_fetched_at = now
        state.http_status = status_code
        state.mime_type = mime_type
        state.fetch_method = fetch_method
        state.etag = headers.get("etag", state.etag)
        state.last_modified = headers.get("last-modified", state.last_modified)
        state.consecutive_errors = 0
        if changed:
            state.content_hash = content_hash
            state.semantic_hash = semantic_hash
            state.last_changed_at = now
        existing = self.session.scalar(
            select(PageSnapshot).where(
                PageSnapshot.url_state_id == state.id,
                PageSnapshot.content_hash == content_hash,
            )
        )
        if existing is not None:
            return existing, False
        compressed = gzip.compress(raw_body, compresslevel=6)
        object_key = ""
        object_sha256 = ""
        storage_tier = "database"
        database_body: bytes | None = compressed
        if self.object_store is not None:
            object_key = (
                f"platform/{state.platform_id}/{now:%Y/%m/%d}/{run.id}/"
                f"{state.id}-{content_hash}.gz"
            )
            stored = self.object_store.put(object_key, compressed, "application/gzip")
            object_sha256 = stored.sha256
            storage_tier = stored.storage_tier
            database_body = None
        snapshot = PageSnapshot(
            url_state_id=state.id,
            run_id=run.id,
            status_code=status_code,
            final_url=final_url,
            mime_type=mime_type,
            encoding=encoding,
            response_headers_json=json_dumps(headers),
            raw_body_gzip=database_body,
            object_key=object_key,
            object_sha256=object_sha256,
            storage_tier=storage_tier,
            raw_expires_at=now + timedelta(days=self.raw_retention_days),
            raw_size=len(raw_body),
            content_hash=content_hash,
            semantic_hash=semantic_hash,
            title=extracted.title,
            extracted_text=extracted.text,
            fetch_method=fetch_method,
            truncated=truncated,
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot, changed

    def save_links(
        self,
        state: UrlState,
        links: list[tuple[str, str, float]],
    ) -> None:
        now = _now()
        observed: set[str] = set()
        for target, anchor, relevance in links:
            canonical = canonicalize_url(target)
            if not canonical or canonical in observed:
                continue
            observed.add(canonical)
            page_link = self.session.scalar(
                select(PageLink).where(
                    PageLink.from_url_state_id == state.id,
                    PageLink.to_url == canonical,
                )
            )
            if page_link is None:
                self.session.add(
                    PageLink(
                        from_url_state_id=state.id,
                        to_url=canonical,
                        anchor_text=anchor,
                        relevance_score=relevance,
                    )
                )
            else:
                page_link.last_seen_at = now
                page_link.anchor_text = anchor or page_link.anchor_text
                page_link.relevance_score = relevance
                page_link.active = True

    def upsert_item(
        self,
        *,
        platform: Platform,
        collection_id: int | None,
        source_state: UrlState,
        run: CrawlRun,
        snapshot: PageSnapshot | None,
        extracted: ExtractedItem,
    ) -> tuple[CatalogItem, str]:
        self._apply_taxonomy_mappings(extracted)
        natural_key = _natural_key(extracted)
        item = self.session.scalar(
            select(CatalogItem).where(
                CatalogItem.platform_id == platform.id,
                CatalogItem.natural_key == natural_key,
            )
        )
        now = _now()
        event_type = "unchanged"
        if item is None:
            item = CatalogItem(
                platform_id=platform.id,
                collection_id=collection_id,
                source_url_state_id=source_state.id,
                natural_key=natural_key,
                external_id=extracted.external_id,
                kind=extracted.kind,
                name=extracted.name,
                status="active",
                first_seen_at=now,
                last_seen_at=now,
            )
            self.session.add(item)
            self.session.flush()
            event_type = "added"
        else:
            previous_status = item.status
            item.last_seen_at = now
            item.missing_full_scans = 0
            item.status = "active"
            item.name = extracted.name
            item.source_url_state_id = source_state.id
            item.collection_id = collection_id or item.collection_id
            if previous_status != "active":
                event_type = "recovered"

        semantic_payload = _semantic_payload(extracted)
        semantic_hash = sha256_text(json_dumps(semantic_payload))
        current = self.session.get(CatalogItemVersion, item.current_version_id) if item.current_version_id else None
        if current and current.semantic_hash == semantic_hash:
            if event_type == "recovered":
                self.session.add(ChangeEvent(run_id=run.id, platform_id=platform.id, item_id=item.id, version_id=current.id, event_type=event_type))
            return item, event_type

        if current is not None:
            current.valid_to = now
            version_no = current.version_no + 1
            event_type = "updated" if event_type == "unchanged" else event_type
        else:
            version_no = 1
        diff = _field_diff(_version_payload(current), semantic_payload)
        version = CatalogItemVersion(
            item_id=item.id,
            run_id=run.id,
            snapshot_id=snapshot.id if snapshot else None,
            semantic_hash=semantic_hash,
            version_no=version_no,
            valid_from=now,
            name=extracted.name,
            description=extracted.description,
            provider=extracted.provider,
            product_type_raw=extracted.product_type_raw,
            product_type=extracted.product_type,
            price_raw=extracted.price_raw,
            delivery_method=extracted.delivery_method,
            refresh_frequency=extracted.refresh_frequency,
            data_period_start=extracted.data_period_start,
            data_period_end=extracted.data_period_end,
            published_at=extracted.published_at,
            source_updated_at=extracted.source_updated_at,
            source_url=canonicalize_url(extracted.source_url),
            source_fields_json=json_dumps(extracted.source_fields),
            normalized_json=json_dumps(extracted.normalized),
            diff_json=json_dumps(diff),
            extraction_confidence=extracted.confidence,
            extractor_version=extracted.extractor_version,
        )
        self.session.add(version)
        self.session.flush()
        item.current_version_id = version.id
        for dimension in extracted.dimensions:
            if not dimension.normalized_value:
                continue
            self.session.add(
                ItemDimension(
                    item_id=item.id,
                    version_id=version.id,
                    dimension_type=dimension.dimension_type,
                    raw_value=dimension.raw_value,
                    normalized_value=dimension.normalized_value,
                    confidence=dimension.confidence,
                    method=dimension.method,
                    taxonomy_version=dimension.taxonomy_version,
                )
            )
        for evidence in extracted.evidence:
            self.session.add(
                FieldEvidence(
                    version_id=version.id,
                    snapshot_id=snapshot.id if snapshot else None,
                    field_name=evidence.field_name,
                    raw_value=evidence.raw_value,
                    locator=evidence.locator,
                    method=evidence.method,
                    confidence=evidence.confidence,
                )
            )
        product_type_confidence = next(
            (dimension.confidence for dimension in extracted.dimensions if dimension.dimension_type == "product_type"),
            extracted.confidence,
        )
        if product_type_confidence < self.review_threshold:
            self.session.add(
                ClassificationReview(
                    item_id=item.id,
                    version_id=version.id,
                    field_name="product_type",
                    proposed_value=extracted.product_type,
                    confidence=product_type_confidence,
                )
            )
        self.session.add(
            ChangeEvent(
                run_id=run.id,
                platform_id=platform.id,
                item_id=item.id,
                version_id=version.id,
                event_type=event_type,
                payload_json=json_dumps({"diff": diff}),
            )
        )
        self._link_canonical_entity(item, extracted)
        return item, event_type

    def _apply_taxonomy_mappings(self, extracted: ExtractedItem) -> None:
        """Apply enabled, administrator-maintained exact mappings before hashing.

        Matching is whitespace/case insensitive.  Applying it before the
        semantic hash makes a mapping correction produce one traceable version
        the next time the source record is observed.
        """

        rows = self.session.scalars(
            select(TaxonomyMapping).where(TaxonomyMapping.enabled.is_(True))
        ).all()
        mappings = {
            (row.dimension_type, normalize_text(row.raw_value).casefold()): row
            for row in rows
        }
        for dimension in extracted.dimensions:
            key = (dimension.dimension_type, normalize_text(dimension.raw_value).casefold())
            mapping = mappings.get(key)
            if mapping is None:
                continue
            dimension.normalized_value = mapping.normalized_value
            dimension.confidence = mapping.confidence
            dimension.method = "taxonomy_mapping"
            if dimension.dimension_type == "product_type":
                extracted.product_type = mapping.normalized_value

    def _link_canonical_entity(self, item: CatalogItem, extracted: ExtractedItem) -> None:
        existing = self.session.scalar(select(EntitySourceLink).where(EntitySourceLink.item_id == item.id))
        if existing is not None:
            return
        normalized_name = normalize_text(extracted.name).casefold()
        provider_normalized = normalize_text(extracted.provider).casefold()
        entity = None
        match_method = "new_entity"
        confidence = 1.0
        if provider_normalized:
            entity = self.session.scalar(
                select(CanonicalEntity).where(
                    CanonicalEntity.kind == extracted.kind,
                    CanonicalEntity.normalized_name == normalized_name,
                    CanonicalEntity.provider_normalized == provider_normalized,
                )
            )
            if entity is not None:
                match_method = "exact_name_provider"
                confidence = 0.995
        if entity is None:
            entity = CanonicalEntity(
                kind=extracted.kind,
                name=extracted.name,
                normalized_name=normalized_name,
                provider=extracted.provider,
                provider_normalized=provider_normalized,
            )
            self.session.add(entity)
            self.session.flush()
        self.session.add(
            EntitySourceLink(
                entity_id=entity.id,
                item_id=item.id,
                match_method=match_method,
                confidence=confidence,
            )
        )
        self.session.flush()

    def record_error(
        self,
        *,
        run_id: str,
        platform_id: int | None,
        url: str,
        stage: str,
        error: Exception | str,
        collection_id: int | None = None,
        retryable: bool = False,
        attempt: int = 1,
        details: dict[str, Any] | None = None,
    ) -> CrawlError:
        record = CrawlError(
            run_id=run_id,
            platform_id=platform_id,
            collection_id=collection_id,
            url=url,
            stage=stage,
            error_type=type(error).__name__ if isinstance(error, Exception) else "Error",
            message=str(error)[:8_000],
            retryable=retryable,
            attempt=attempt,
            details_json=json_dumps(details or {}),
        )
        self.session.add(record)
        return record

    def reconcile_missing_items(
        self,
        *,
        run: CrawlRun,
        platform: Platform,
        coverage_complete: bool,
        threshold: int = 3,
    ) -> int:
        """Mark missing items only after repeated successful complete scans."""

        if run.mode != "full" or not coverage_complete:
            return 0
        candidates = self.session.scalars(
            select(CatalogItem).where(
                CatalogItem.platform_id == platform.id,
                CatalogItem.status == "active",
                CatalogItem.last_seen_at < run.started_at,
            )
        ).all()
        retired = 0
        for item in candidates:
            item.missing_full_scans += 1
            if item.missing_full_scans >= threshold:
                item.status = "inactive"
                retired += 1
                self.session.add(
                    ChangeEvent(
                        run_id=run.id,
                        platform_id=platform.id,
                        item_id=item.id,
                        version_id=item.current_version_id,
                        event_type="removed",
                        payload_json=json_dumps({"reason": f"missing from {threshold} complete full scans"}),
                    )
                )
        self.session.flush()
        return retired

    def finish_run(self, run: CrawlRun, status: str, stats: dict[str, Any], error_summary: str = "") -> None:
        run.status = status
        run.stats_json = json_dumps(stats)
        run.error_summary = error_summary[:8_000]
        run.finished_at = _now()
