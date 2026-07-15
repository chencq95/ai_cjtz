"""Seed and maintain the source-platform and collection registry."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from .database import init_database, session_factory, session_scope
from .models import Platform, SourceCollection


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "config" / "platforms.csv"
SITE_RULES = ROOT / "config" / "site_rules.json"


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _load_rows(source: Path) -> list[dict[str, Any]]:
    if source.suffix.lower() == ".json":
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            payload = payload.get("platforms", [])
        if not isinstance(payload, list):
            raise ValueError("JSON seed must be a list or contain a 'platforms' list")
        return [dict(row) for row in payload]
    with source.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _upsert_collection(
    session: Any,
    *,
    platform_id: int,
    code: str,
    name: str,
    object_kind: str,
    entry_url: str,
    adapter: str,
    enabled: bool,
    pagination_mode: str = "auto",
    coverage_status: str = "unknown",
    notes: str = "",
) -> SourceCollection:
    collection = session.scalar(
        select(SourceCollection).where(
            SourceCollection.platform_id == platform_id,
            SourceCollection.code == code,
        )
    )
    if collection is None:
        collection = SourceCollection(
            platform_id=platform_id,
            code=code,
            name=name,
            object_kind=object_kind,
            entry_url=entry_url,
        )
        session.add(collection)
    collection.name = name
    collection.object_kind = object_kind
    collection.entry_url = entry_url
    collection.adapter = adapter
    collection.adapter_version = adapter if adapter != "generic" else "generic-v1"
    collection.enabled = enabled
    collection.pagination_mode = pagination_mode
    collection.coverage_status = coverage_status
    if notes:
        collection.notes = notes
    return collection


def seed_platforms(
    settings: object,
    source: Path | None = None,
    update_existing: bool = True,
) -> dict[str, int | str]:
    """Idempotently import all 38 platforms and auditable collection placeholders."""

    source_path = Path(source) if source else DEFAULT_SOURCE
    if not source_path.exists():
        raise FileNotFoundError(f"platform seed not found: {source_path}")
    rows = _load_rows(source_path)
    init_database(settings)
    factory = session_factory(settings)
    inserted = updated = skipped = 0
    fields = (
        "province", "city", "name", "operator", "source_url", "canonical_url",
        "url_status", "render_mode", "adapter", "notes",
    )

    with session_scope(factory) as session:
        for row in rows:
            platform_id = int(row["id"])
            platform = session.get(Platform, platform_id)
            if platform is None:
                values = {field: (row.get(field) or "").strip() for field in fields}
                platform = Platform(
                    id=platform_id,
                    enabled=_as_bool(row.get("enabled"), bool(values["canonical_url"])),
                    **values,
                )
                session.add(platform)
                inserted += 1
            elif update_existing:
                for field in fields:
                    if field in row:
                        setattr(platform, field, (row.get(field) or "").strip())
                platform.enabled = _as_bool(row.get("enabled"), platform.enabled)
                updated += 1
            else:
                skipped += 1

            # Seeding is also used to apply new site rules in an existing
            # database.  Do not erase a crawl's auditable terminal conclusion
            # merely because the seed CSV was re-read during deployment.
            terminal_statuses = {"complete", "blocked", "offline", "out_of_scope"}
            if platform_id == 38:
                platform.source_role = "reference"
                platform.onboarding_status = "out_of_scope"
            elif not platform.enabled or platform.url_status == "missing_url":
                platform.onboarding_status = "blocked"
            elif platform.url_status in {"tls_cert_expired", "site_expired"}:
                platform.onboarding_status = "offline"
            elif platform.onboarding_status not in terminal_statuses:
                platform.onboarding_status = "pending_audit"

            entry_url = (row.get("canonical_url") or row.get("source_url") or "").strip()
            adapter = (row.get("adapter") or "generic").strip()
            if entry_url:
                _upsert_collection(
                    session,
                    platform_id=platform_id,
                    code="public-catalog-auto",
                    name="公开目录自动发现",
                    object_kind="mixed",
                    entry_url=entry_url,
                    adapter=adapter,
                    enabled=True,
                    notes="待通过栏目发现与分页对账拆分为明确集合",
                )
            for code, name, kind in (
                ("data-products", "数据产品", "product"),
                ("data-components", "数据组件", "component"),
                ("data-scenarios", "数据场景", "scenario"),
                ("demands", "需求", "demand"),
                ("providers", "数商", "provider"),
            ):
                _upsert_collection(
                    session,
                    platform_id=platform_id,
                    code=code,
                    name=name,
                    object_kind=kind,
                    entry_url=entry_url,
                    adapter=adapter,
                    enabled=False,
                    coverage_status="out_of_scope" if platform_id == 38 else "unknown",
                    notes="完成栏目入口和分页规则核验后启用",
                )

        session.flush()
        if SITE_RULES.exists():
            rules = json.loads(SITE_RULES.read_text(encoding="utf-8"))
            for platform_id_raw, rule in rules.items():
                platform_id = int(platform_id_raw)
                platform = session.get(Platform, platform_id)
                if platform is None:
                    continue
                auto_collection = session.scalar(
                    select(SourceCollection).where(
                        SourceCollection.platform_id == platform_id,
                        SourceCollection.code == "public-catalog-auto",
                    )
                )
                if auto_collection is not None:
                    auto_collection.enabled = False
                    auto_collection.coverage_status = "out_of_scope"
                    auto_collection.notes = "已由站点专用栏目规则覆盖，避免通用入口重复计数"
                platform.adapter = str(rule.get("adapter") or platform.adapter)
                platform.render_mode = str(rule.get("render_mode") or platform.render_mode)
                for item in rule.get("collections", []):
                    _upsert_collection(
                        session,
                        platform_id=platform_id,
                        code=str(item["code"]),
                        name=str(item["name"]),
                        object_kind=str(item["object_kind"]),
                        entry_url=str(item["entry_url"]),
                        adapter=platform.adapter,
                        enabled=bool(item.get("enabled", True)),
                        pagination_mode=str(item.get("pagination_mode", "auto")),
                        notes="已配置公开栏目入口；完整性以运行对账结果为准",
                    )

    return {"source": str(source_path), "rows": len(rows), "inserted": inserted, "updated": updated, "skipped": skipped}
