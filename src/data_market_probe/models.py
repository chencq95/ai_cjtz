"""Relational data model for crawl evidence, versioned catalog records and coverage."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid4_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Platform(Base, TimestampMixin):
    __tablename__ = "platform"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    province: Mapped[str] = mapped_column(String(32), default="")
    city: Mapped[str] = mapped_column(String(32), default="")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    operator: Mapped[str] = mapped_column(String(160), default="")
    source_url: Mapped[str] = mapped_column(Text, default="")
    canonical_url: Mapped[str] = mapped_column(Text, default="")
    url_status: Mapped[str] = mapped_column(String(32), default="unverified")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    render_mode: Mapped[str] = mapped_column(String(16), default="auto")
    adapter: Mapped[str] = mapped_column(String(64), default="generic")
    notes: Mapped[str] = mapped_column(Text, default="")
    source_role: Mapped[str] = mapped_column(String(32), default="exchange")
    onboarding_status: Mapped[str] = mapped_column(String(24), default="pending_audit", index=True)
    legal_review_status: Mapped[str] = mapped_column(String(24), default="pending")
    default_rate_limit: Mapped[float] = mapped_column(Float, default=1.0)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=1)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    collections: Mapped[list["SourceCollection"]] = relationship(
        back_populates="platform", cascade="all, delete-orphan"
    )


class SourceCollection(Base, TimestampMixin):
    """A declared catalog boundary whose completeness can be audited."""

    __tablename__ = "source_collection"
    __table_args__ = (
        UniqueConstraint("platform_id", "code", name="uq_collection_platform_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platform.id"), index=True)
    code: Mapped[str] = mapped_column(String(96), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    object_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_url: Mapped[str] = mapped_column(Text, nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), default="generic")
    expected_count: Mapped[int | None] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    access_policy: Mapped[str] = mapped_column(String(32), default="public")
    coverage_status: Mapped[str] = mapped_column(String(24), default="unknown")
    last_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str] = mapped_column(Text, default="")
    pagination_mode: Mapped[str] = mapped_column(String(32), default="auto")
    stable_key_rule: Mapped[str] = mapped_column(String(64), default="auto")
    source_update_field: Mapped[str] = mapped_column(String(96), default="")
    adapter_version: Mapped[str] = mapped_column(String(32), default="generic-v1")
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    platform: Mapped[Platform] = relationship(back_populates="collections")


class CrawlRun(Base):
    __tablename__ = "crawl_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    mode: Mapped[str] = mapped_column(String(24), default="incremental")
    trigger: Mapped[str] = mapped_column(String(24), default="manual")
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    stats_json: Mapped[str] = mapped_column(Text, default="{}")
    error_summary: Mapped[str] = mapped_column(Text, default="")


class PlatformRun(Base):
    __tablename__ = "platform_run"
    __table_args__ = (
        UniqueConstraint("run_id", "platform_id", name="uq_platform_run"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platform.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="running")
    coverage_status: Mapped[str] = mapped_column(String(24), default="unknown")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    urls_discovered: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    pages_changed: Mapped[int] = mapped_column(Integer, default=0)
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    expected_count: Mapped[int | None] = mapped_column(Integer)
    observed_count: Mapped[int | None] = mapped_column(Integer)
    completeness_json: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")


class UrlState(Base):
    __tablename__ = "url_state"
    __table_args__ = (
        UniqueConstraint("platform_id", "canonical_url", name="uq_platform_url"),
        Index("ix_url_due", "platform_id", "next_fetch_at", "active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platform.id"), index=True)
    collection_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_collection.id"), index=True
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    discovered_from: Mapped[str] = mapped_column(Text, default="")
    anchor_text: Mapped[str] = mapped_column(Text, default="")
    page_role: Mapped[str] = mapped_column(String(24), default="unknown")
    depth: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str] = mapped_column(Text, default="")
    last_modified: Mapped[str] = mapped_column(Text, default="")
    http_status: Mapped[int | None] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(160), default="")
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    semantic_hash: Mapped[str] = mapped_column(String(64), default="")
    fetch_method: Mapped[str] = mapped_column(String(24), default="http")
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    consecutive_errors: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_missing_full_scans: Mapped[int] = mapped_column(Integer, default=0)


class PageSnapshot(Base):
    __tablename__ = "page_snapshot"
    __table_args__ = (
        UniqueConstraint("url_state_id", "content_hash", name="uq_url_content_hash"),
        Index("ix_snapshot_run_fetched", "run_id", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url_state_id: Mapped[int] = mapped_column(ForeignKey("url_state.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status_code: Mapped[int] = mapped_column(Integer)
    final_url: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), default="")
    encoding: Mapped[str] = mapped_column(String(40), default="")
    response_headers_json: Mapped[str] = mapped_column(Text, default="{}")
    raw_body_gzip: Mapped[bytes | None] = mapped_column(LargeBinary)
    object_key: Mapped[str] = mapped_column(Text, default="")
    object_sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_tier: Mapped[str] = mapped_column(String(24), default="database")
    raw_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_size: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    semantic_hash: Mapped[str] = mapped_column(String(64), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    extracted_text: Mapped[str] = mapped_column(Text, default="")
    fetch_method: Mapped[str] = mapped_column(String(24), default="http")
    parser_version: Mapped[str] = mapped_column(String(32), default="generic-v1")
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)


class PageLink(Base):
    __tablename__ = "page_link"
    __table_args__ = (
        UniqueConstraint("from_url_state_id", "to_url", name="uq_page_link"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_url_state_id: Mapped[int] = mapped_column(ForeignKey("url_state.id"), index=True)
    to_url: Mapped[str] = mapped_column(Text, nullable=False)
    anchor_text: Mapped[str] = mapped_column(Text, default="")
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CatalogItem(Base):
    __tablename__ = "catalog_item"
    __table_args__ = (
        UniqueConstraint("platform_id", "natural_key", name="uq_item_natural_key"),
        Index("ix_item_filters", "kind", "status", "platform_id"),
        Index("ix_item_name", "name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platform.id"), index=True)
    collection_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_collection.id"), index=True
    )
    source_url_state_id: Mapped[int | None] = mapped_column(
        ForeignKey("url_state.id"), index=True
    )
    natural_key: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), default="")
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    missing_full_scans: Mapped[int] = mapped_column(Integer, default=0)
    current_version_id: Mapped[int | None] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CanonicalEntity(Base, TimestampMixin):
    """Cross-platform concept linked only by strong deterministic evidence."""

    __tablename__ = "canonical_entity"
    __table_args__ = (
        Index("ix_canonical_identity", "kind", "normalized_name", "provider_normalized"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(400), nullable=False)
    provider: Mapped[str] = mapped_column(Text, default="")
    provider_normalized: Mapped[str] = mapped_column(String(400), default="")
    status: Mapped[str] = mapped_column(String(24), default="active")


class EntitySourceLink(Base):
    __tablename__ = "entity_source_link"
    __table_args__ = (UniqueConstraint("item_id", name="uq_entity_source_item"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[str] = mapped_column(ForeignKey("canonical_entity.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_item.id"), index=True)
    match_method: Mapped[str] = mapped_column(String(32), default="new_entity")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogItemVersion(Base):
    __tablename__ = "catalog_item_version"
    __table_args__ = (
        UniqueConstraint("item_id", "semantic_hash", name="uq_item_semantic_hash"),
        Index("ix_version_dates", "published_at", "source_updated_at", "detected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_item.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("page_snapshot.id"), index=True)
    semantic_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, default=1)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(Text, default="")
    product_type_raw: Mapped[str] = mapped_column(Text, default="")
    product_type: Mapped[str] = mapped_column(String(64), default="other", index=True)
    price_raw: Mapped[str] = mapped_column(Text, default="")
    delivery_method: Mapped[str] = mapped_column(String(96), default="")
    refresh_frequency: Mapped[str] = mapped_column(String(96), default="")
    data_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_fields_json: Mapped[str] = mapped_column(Text, default="{}")
    normalized_json: Mapped[str] = mapped_column(Text, default="{}")
    diff_json: Mapped[str] = mapped_column(Text, default="{}")
    extraction_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    extractor_version: Mapped[str] = mapped_column(String(32), default="generic-v1")


class ItemDimension(Base):
    __tablename__ = "item_dimension"
    __table_args__ = (
        UniqueConstraint(
            "item_id", "version_id", "dimension_type", "normalized_value",
            name="uq_item_dimension",
        ),
        Index("ix_dimension_filter", "dimension_type", "normalized_value"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_item.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("catalog_item_version.id"), index=True)
    dimension_type: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, default="")
    normalized_value: Mapped[str] = mapped_column(String(160), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    method: Mapped[str] = mapped_column(String(32), default="source")
    taxonomy_version: Mapped[str] = mapped_column(String(32), default="v1")


class FieldEvidence(Base):
    __tablename__ = "field_evidence"
    __table_args__ = (
        Index("ix_evidence_version_field", "version_id", "field_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("catalog_item_version.id"), index=True)
    snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("page_snapshot.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, default="")
    locator: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(32), default="rule")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class ChangeEvent(Base):
    __tablename__ = "change_event"
    __table_args__ = (Index("ix_change_cursor", "created_at", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platform.id"), index=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_item.id"), index=True)
    version_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item_version.id"))
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrawlError(Base):
    __tablename__ = "crawl_error"
    __table_args__ = (Index("ix_error_run_platform", "run_id", "platform_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    platform_id: Mapped[int | None] = mapped_column(ForeignKey("platform.id"), index=True)
    collection_id: Mapped[int | None] = mapped_column(ForeignKey("source_collection.id"))
    url: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    error_type: Mapped[str] = mapped_column(String(96), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdapterVersion(Base, TimestampMixin):
    __tablename__ = "adapter_version"
    __table_args__ = (UniqueConstraint("platform_id", "name", "version", name="uq_adapter_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[int] = mapped_column(ForeignKey("platform.id"), index=True)
    name: Mapped[str] = mapped_column(String(96), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CrawlSchedule(Base, TimestampMixin):
    __tablename__ = "crawl_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    mode: Mapped[str] = mapped_column(String(24), default="incremental")
    platform_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    max_pages: Mapped[int | None] = mapped_column(Integer)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class CrawlTask(Base, TimestampMixin):
    __tablename__ = "crawl_task"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    celery_task_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    task_type: Mapped[str] = mapped_column(String(32), default="crawl")
    mode: Mapped[str] = mapped_column(String(24), default="incremental")
    platform_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    max_pages: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    requested_by: Mapped[str] = mapped_column(String(128), default="system")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str] = mapped_column(Text, default="")


class RunLog(Base):
    __tablename__ = "run_log"
    __table_args__ = (Index("ix_run_log_cursor", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class User(Base, TimestampMixin):
    __tablename__ = "app_user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(String(24), default="readonly")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_created", "created_at", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    username: Mapped[str] = mapped_column(String(128), default="system")
    action: Mapped[str] = mapped_column(String(96), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str] = mapped_column(String(128), default="")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    ip_address: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Alert(Base, TimestampMixin):
    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    severity: Mapped[str] = mapped_column(String(24), default="warning", index=True)
    alert_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    message: Mapped[str] = mapped_column(Text, default="")
    platform_id: Mapped[int | None] = mapped_column(ForeignKey("platform.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="open", index=True)
    acknowledged_by: Mapped[str] = mapped_column(String(128), default="")
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassificationReview(Base, TimestampMixin):
    __tablename__ = "classification_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_item.id"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("catalog_item_version.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    proposed_value: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    reviewer: Mapped[str] = mapped_column(String(128), default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str] = mapped_column(Text, default="")


class TaxonomyMapping(Base, TimestampMixin):
    __tablename__ = "taxonomy_mapping"
    __table_args__ = (UniqueConstraint("dimension_type", "raw_value", name="uq_taxonomy_mapping"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dimension_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_value: Mapped[str] = mapped_column(String(240), nullable=False)
    normalized_value: Mapped[str] = mapped_column(String(240), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
