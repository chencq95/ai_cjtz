"""Source-registry-driven, evidence-preserving incremental crawler."""

from __future__ import annotations

import asyncio
import heapq
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import func, or_, select

from .database import init_database, session_factory, session_scope
from .extraction import ExtractedPage, extract_html, extract_json, link_relevance
from .fetching import (
    BrowserRenderer,
    FetchFailure,
    FetchResult,
    HttpFetcher,
    RobotsDenied,
    allowed_host_for_url,
    domain_family,
    looks_like_spa,
)
from .models import (
    Alert,
    CatalogItem,
    CrawlRun,
    PageLink,
    Platform,
    PlatformRun,
    SourceCollection,
    UrlState,
)
from .object_store import ObjectStore, build_object_store
from .repository import CatalogRepository
from .seed import seed_platforms
from .utils import canonicalize_url, json_dumps, normalize_text, registrable_host


logger = logging.getLogger(__name__)


@dataclass(order=True, slots=True)
class QueueEntry:
    priority: float
    sequence: int
    url: str = field(compare=False)
    depth: int = field(compare=False, default=0)
    discovered_from: str = field(compare=False, default="")
    anchor_text: str = field(compare=False, default="")
    collection_id: int | None = field(compare=False, default=None)
    page_role: str = field(compare=False, default="unknown")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decode(result: FetchResult) -> str:
    candidates = [result.encoding, "utf-8", "gb18030", "gbk"]
    for encoding in candidates:
        if not encoding:
            continue
        try:
            return result.body.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return result.body.decode("utf-8", errors="replace")


def _extract_pdf_text(body: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(io.BytesIO(body))
        return normalize_text(" ".join((page.extract_text() or "") for page in reader.pages[:500]))
    except Exception:
        return ""


def _extract_xml(text: str, url: str) -> ExtractedPage:
    soup = BeautifulSoup(text, "xml")
    links: list[tuple[str, str, float]] = []
    for node in soup.find_all("loc"):
        target = canonicalize_url(url, node.get_text(strip=True))
        if target:
            links.append((target, "sitemap", max(2.0, link_relevance(target, "sitemap"))))
    return ExtractedPage(title="sitemap", text=normalize_text(soup.get_text(" ")), links=links, items=[])


def _extract_result(result: FetchResult, platform: Platform) -> ExtractedPage:
    text = _decode(result)
    mime = result.mime_type.lower()
    stripped = result.body.lstrip()
    if "json" in mime or stripped.startswith((b"{", b"[")):
        try:
            return extract_json(
                text,
                result.final_url,
                platform_province=platform.province,
                platform_city=platform.city,
            )
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
    if "xml" in mime or stripped.startswith(b"<?xml"):
        return _extract_xml(text, result.final_url)
    if "pdf" in mime or result.body.startswith(b"%PDF"):
        return ExtractedPage(title=Path(urlsplit(result.final_url).path).name, text=_extract_pdf_text(result.body), links=[], items=[])
    return extract_html(
        text,
        result.final_url,
        platform_province=platform.province,
        platform_city=platform.city,
    )


def _page_role(url: str, anchor: str = "", mime: str = "") -> str:
    value = f"{url} {anchor}".lower()
    if "json" in mime or "/api/" in value:
        return "api"
    if "sitemap" in value or value.endswith(".xml"):
        return "sitemap"
    if "pdf" in mime or re.search(r"\.pdf(?:\?|$)", value):
        return "document"
    if any(term in value for term in ("detail", "info", "view", "productdetail", "product-detail")):
        return "detail"
    if any(term in value for term in ("list", "catalog", "product", "goods", "dataset", "component", "scene", "scenario", "demand", "产品", "商品", "场景")):
        return "listing"
    return "unknown"


def _due_at(role: str, changed: bool, full: bool) -> datetime:
    now = _utcnow()
    if role in {"entry", "listing", "api", "sitemap", "unknown"}:
        return now + timedelta(days=1)
    if changed:
        return now + timedelta(days=2)
    return now + timedelta(days=7 if role == "detail" else 14)


def _queue_score(url: str, anchor: str, role: str, depth: int) -> float:
    score = link_relevance(url, anchor)
    score += {"entry": 8.0, "sitemap": 7.0, "listing": 5.0, "api": 5.0, "detail": 4.0}.get(role, 0.0)
    score -= depth * 0.15
    return score


def _should_queue(
    *,
    url: str,
    anchor: str,
    score: float,
    depth: int,
    parent_role: str,
    max_depth: int,
) -> bool:
    if depth > max_depth or not url:
        return False
    value = f"{url} {anchor}".lower()
    if re.search(r"\.(?:jpg|jpeg|png|gif|svg|css|woff2?|ttf|mp4|mp3|zip|rar|7z|xlsx?|csv|docx?)(?:\?|$)", value):
        return False
    if depth <= 1:
        return score > -5.0
    if parent_role in {"listing", "sitemap", "api"} and (
        score > -0.1 or anchor in {"下一页", "下页", "末页", "更多"} or anchor.isdigit()
    ):
        return True
    return score >= 1.0


async def _process_capture(
    *,
    capture: FetchResult,
    platform: Platform,
    collection_id: int | None,
    parent_state: UrlState,
    run: CrawlRun,
    repository: CatalogRepository,
    platform_run: PlatformRun,
) -> None:
    if capture.status_code != 200 or not capture.body:
        return
    state = repository.get_or_create_url(
        platform_id=platform.id,
        collection_id=collection_id,
        url=capture.final_url,
        discovered_from=parent_state.canonical_url,
        anchor_text="browser-network",
        depth=parent_state.depth + 1,
        page_role="api" if "network" in capture.method else "listing",
    )
    extracted = _extract_result(capture, platform)
    snapshot, changed = repository.save_snapshot(
        state=state,
        run=run,
        status_code=capture.status_code,
        final_url=capture.final_url,
        mime_type=capture.mime_type,
        encoding=capture.encoding,
        headers=capture.headers,
        raw_body=capture.body,
        extracted=extracted,
        fetch_method=capture.method,
        truncated=capture.truncated,
    )
    repository.save_links(state, extracted.links)
    state.next_fetch_at = _due_at(state.page_role, changed, run.mode == "full")
    platform_run.pages_fetched += 1
    platform_run.pages_changed += int(changed)
    for extracted_item in extracted.items:
        item_state = repository.get_or_create_url(
            platform_id=platform.id,
            collection_id=collection_id,
            url=extracted_item.source_url or state.canonical_url,
            discovered_from=state.canonical_url,
            anchor_text=extracted_item.name,
            depth=state.depth + 1,
            page_role="detail",
        )
        _item, event = repository.upsert_item(
            platform=platform,
            collection_id=collection_id,
            source_state=item_state,
            run=run,
            snapshot=snapshot,
            extracted=extracted_item,
        )
        platform_run.items_seen += 1
        platform_run.items_new += int(event == "added")
        platform_run.items_updated += int(event in {"updated", "recovered"})


async def _crawl_platform(
    *,
    settings: object,
    factory: Any,
    run_id: str,
    platform_id: int,
    fetcher: HttpFetcher,
    renderer: BrowserRenderer,
    object_store: ObjectStore | None,
    fetch_semaphore: asyncio.Semaphore,
    full: bool,
    max_pages: int,
    cancel_check: Callable[[], bool] | None,
) -> dict[str, Any]:
    session = factory()
    try:
        repository = CatalogRepository(
            session,
            object_store=object_store,
            raw_retention_days=int(getattr(settings, "raw_retention_days", 365)),
            review_threshold=float(getattr(settings, "classification_review_threshold", 0.80)),
        )
        run = session.get(CrawlRun, run_id)
        platform = session.get(Platform, platform_id)
        if run is None or platform is None:
            raise RuntimeError(f"crawl context missing for platform {platform_id}")
        platform_run = repository.start_platform_run(run, platform.id)
        session.commit()
        if platform.source_role == "reference":
            platform_run.status = "out_of_scope"
            platform_run.coverage_status = "out_of_scope"
            platform_run.notes = "参考来源，不递归抓取国家数据局政府网站"
            platform_run.finished_at = _utcnow()
            session.commit()
            return {"platform_id": platform.id, "status": "out_of_scope", "coverage": "out_of_scope", "pages": 0, "items": 0, "errors": 0}
        entry_url = canonicalize_url(platform.canonical_url or platform.source_url)
        if not platform.enabled or not entry_url:
            platform_run.status = "no_url"
            platform_run.coverage_status = "blocked"
            platform_run.notes = "截图未提供可采集官网网址或来源已停用"
            platform_run.finished_at = _utcnow()
            session.commit()
            return {"platform_id": platform.id, "status": "no_url", "pages": 0, "items": 0, "errors": 0}

        allowed_families = {domain_family(registrable_host(entry_url))}
        if not allowed_host_for_url(entry_url, allowed_families):
            repository.record_error(
                run_id=run.id,
                platform_id=platform.id,
                collection_id=None,
                url=entry_url,
                stage="ssrf_guard",
                error="入口 URL 未通过公网地址安全校验",
                retryable=False,
            )
            platform_run.status = "blocked"
            platform_run.coverage_status = "blocked"
            platform_run.error_count = 1
            platform_run.notes = "入口 URL 未通过公网地址安全校验，未发起网络请求"
            platform_run.finished_at = _utcnow()
            session.commit()
            return {
                "platform_id": platform.id,
                "name": platform.name,
                "status": "blocked",
                "coverage": "blocked",
                "pages": 0,
                "items_seen": 0,
                "items_new": 0,
                "items_updated": 0,
                "errors": 1,
                "page_limit_hit": False,
                "cancelled": False,
            }
        fetcher.rate_limiter.set_rate(entry_url, platform.default_rate_limit)
        collections = session.scalars(
            select(SourceCollection).where(
                SourceCollection.platform_id == platform.id,
                SourceCollection.enabled.is_(True),
            )
        ).all()
        frontier: list[QueueEntry] = []
        enqueued: set[str] = set()
        visited: set[str] = set()
        sequence = 0

        def enqueue(
            url: str,
            *,
            depth: int,
            discovered_from: str = "",
            anchor: str = "",
            collection_id: int | None = None,
            role: str = "unknown",
            force: bool = False,
        ) -> None:
            nonlocal sequence
            canonical = canonicalize_url(url)
            if not canonical or canonical in enqueued or canonical in visited:
                return
            if not allowed_host_for_url(canonical, allowed_families):
                return
            score = _queue_score(canonical, anchor, role, depth)
            if not force and not _should_queue(
                url=canonical,
                anchor=anchor,
                score=score,
                depth=depth,
                parent_role=role,
                max_depth=int(getattr(settings, "max_crawl_depth", 8)),
            ):
                return
            sequence += 1
            enqueued.add(canonical)
            heapq.heappush(
                frontier,
                QueueEntry(-score, sequence, canonical, depth, discovered_from, anchor, collection_id, role),
            )

        default_collection_id = collections[0].id if collections else None
        enqueue(entry_url, depth=0, collection_id=default_collection_id, role="entry", force=True)
        for collection in collections:
            enqueue(collection.entry_url, depth=0, collection_id=collection.id, role="entry", force=True)
        for suffix in ("/sitemap.xml", "/sitemap_index.xml"):
            parts = urlsplit(entry_url)
            sitemap = f"{parts.scheme}://{parts.netloc}{suffix}"
            enqueue(sitemap, depth=0, collection_id=default_collection_id, role="sitemap", force=True)

        if not full:
            now = _utcnow()
            known_states = session.scalars(
                select(UrlState)
                .where(
                    UrlState.platform_id == platform.id,
                    UrlState.active.is_(True),
                    or_(UrlState.next_fetch_at.is_(None), UrlState.next_fetch_at <= now),
                )
                .order_by(UrlState.next_fetch_at.asc())
                .limit(max_pages)
            ).all()
            for known in known_states:
                enqueue(
                    known.canonical_url,
                    depth=known.depth,
                    discovered_from=known.discovered_from,
                    anchor=known.anchor_text,
                    collection_id=known.collection_id,
                    role=known.page_role,
                    force=True,
                )

        attempts = fetched = errors = 0
        hit_limit = False
        was_cancelled = False
        root_blocked = False
        while frontier:
            if cancel_check is not None and cancel_check():
                was_cancelled = True
                break
            if attempts >= max_pages:
                hit_limit = True
                break
            entry = heapq.heappop(frontier)
            enqueued.discard(entry.url)
            if entry.url in visited:
                continue
            visited.add(entry.url)
            attempts += 1
            state = repository.get_or_create_url(
                platform_id=platform.id,
                collection_id=entry.collection_id,
                url=entry.url,
                discovered_from=entry.discovered_from,
                anchor_text=entry.anchor_text,
                depth=entry.depth,
                page_role=entry.page_role,
            )
            session.commit()
            try:
                async with fetch_semaphore:
                    result = await fetcher.fetch(
                        entry.url,
                        allowed_families=allowed_families,
                        etag=state.etag,
                        last_modified=state.last_modified,
                    )
                state.robots_allowed = True
                if result.status_code == 304:
                    repository.mark_not_modified(state, 304, result.headers)
                    state.next_fetch_at = _due_at(state.page_role, False, full)
                    fetched += 1
                    platform_run.pages_fetched += 1
                    stored_links = session.scalars(
                        select(PageLink).where(
                            PageLink.from_url_state_id == state.id,
                            PageLink.active.is_(True),
                        )
                    ).all()
                    for link in stored_links:
                        role = _page_role(link.to_url, link.anchor_text)
                        enqueue(link.to_url, depth=entry.depth + 1, discovered_from=entry.url, anchor=link.anchor_text, collection_id=entry.collection_id, role=role)
                    platform_run.urls_discovered = len(visited) + len(frontier)
                    session.commit()
                    continue
                if result.status_code >= 400:
                    if entry.page_role == "sitemap" and result.status_code in {404, 410}:
                        state.http_status = result.status_code
                        state.last_fetched_at = _utcnow()
                        state.next_fetch_at = _utcnow() + timedelta(days=30)
                        session.commit()
                        continue
                    errors += 1
                    state.http_status = result.status_code
                    state.last_fetched_at = _utcnow()
                    state.consecutive_errors += 1
                    repository.record_error(
                        run_id=run.id,
                        platform_id=platform.id,
                        collection_id=entry.collection_id,
                        url=entry.url,
                        stage="http_status",
                        error=f"HTTP {result.status_code}",
                        retryable=result.status_code in {408, 425, 429, 500, 502, 503, 504},
                    )
                    if entry.depth == 0 and result.status_code in {401, 403, 418, 451}:
                        root_blocked = True
                    session.commit()
                    continue

                collection_requires_browser = False
                if entry.collection_id is not None:
                    collection_row = session.get(SourceCollection, entry.collection_id)
                    collection_requires_browser = bool(
                        collection_row and collection_row.pagination_mode == "browser"
                    )
                should_render = (
                    renderer.available
                    and platform.render_mode in {"auto", "browser"}
                    and (
                        platform.render_mode == "browser"
                        or collection_requires_browser
                        or looks_like_spa(result.body, entry.url, result.mime_type)
                    )
                )
                if should_render:
                    try:
                        async with fetch_semaphore:
                            rendered = await renderer.render(entry.url, allowed_families=allowed_families)
                        # A formal browser adapter owns the rendered DOM even
                        # when the SSR shell happens to be larger than it.
                        # Otherwise public cards can be present in the browser
                        # result but silently disappear from extraction.
                        if (
                            platform.render_mode == "browser"
                            or collection_requires_browser
                            or len(rendered.body) >= len(result.body)
                        ):
                            result = rendered
                    except FetchFailure as browser_error:
                        repository.record_error(
                            run_id=run.id,
                            platform_id=platform.id,
                            collection_id=entry.collection_id,
                            url=entry.url,
                            stage=browser_error.stage,
                            error=browser_error,
                            retryable=False,
                        )
                        errors += 1

                extracted = _extract_result(result, platform)
                if entry.collection_id:
                    total_match = re.search(r"共\s*([0-9,，]+)\s*(?:条|个)", extracted.text)
                    if total_match:
                        collection_row = session.get(SourceCollection, entry.collection_id)
                        if collection_row is not None:
                            collection_row.expected_count = int(total_match.group(1).replace(",", "").replace("，", ""))
                            collection_row.last_run_at = _utcnow()
                actual_role = _page_role(result.final_url, entry.anchor_text, result.mime_type)
                if entry.page_role == "entry":
                    actual_role = "entry"
                state.page_role = actual_role
                snapshot, changed = repository.save_snapshot(
                    state=state,
                    run=run,
                    status_code=result.status_code,
                    final_url=result.final_url,
                    mime_type=result.mime_type,
                    encoding=result.encoding,
                    headers=result.headers,
                    raw_body=result.body,
                    extracted=extracted,
                    fetch_method=result.method,
                    truncated=result.truncated,
                )
                repository.save_links(state, extracted.links)
                state.next_fetch_at = _due_at(actual_role, changed, full)
                fetched += 1
                platform_run.pages_fetched += 1
                platform_run.pages_changed += int(changed)

                for target, anchor, relevance in extracted.links:
                    role = _page_role(target, anchor)
                    if _should_queue(
                        url=target,
                        anchor=anchor,
                        score=relevance,
                        depth=entry.depth + 1,
                        parent_role=actual_role,
                        max_depth=int(getattr(settings, "max_crawl_depth", 8)),
                    ):
                        enqueue(target, depth=entry.depth + 1, discovered_from=entry.url, anchor=anchor, collection_id=entry.collection_id, role=role, force=True)

                for extracted_item in extracted.items:
                    item_state = repository.get_or_create_url(
                        platform_id=platform.id,
                        collection_id=entry.collection_id,
                        url=extracted_item.source_url or state.canonical_url,
                        discovered_from=state.canonical_url,
                        anchor_text=extracted_item.name,
                        depth=state.depth + 1,
                        page_role="detail",
                    )
                    _item, event = repository.upsert_item(
                        platform=platform,
                        collection_id=entry.collection_id,
                        source_state=item_state,
                        run=run,
                        snapshot=snapshot,
                        extracted=extracted_item,
                    )
                    platform_run.items_seen += 1
                    platform_run.items_new += int(event == "added")
                    platform_run.items_updated += int(event in {"updated", "recovered"})
                    enqueue(extracted_item.source_url, depth=entry.depth + 1, discovered_from=entry.url, anchor=extracted_item.name, collection_id=entry.collection_id, role="detail", force=True)

                for capture in result.captured:
                    await _process_capture(
                        capture=capture,
                        platform=platform,
                        collection_id=entry.collection_id,
                        parent_state=state,
                        run=run,
                        repository=repository,
                        platform_run=platform_run,
                    )
                platform_run.urls_discovered = len(visited) + len(frontier)
                session.commit()
            except RobotsDenied as exc:
                errors += 1
                state.robots_allowed = False
                repository.record_error(run_id=run.id, platform_id=platform.id, collection_id=entry.collection_id, url=entry.url, stage="robots", error=exc, retryable=False)
                if entry.depth == 0:
                    root_blocked = True
                session.commit()
            except FetchFailure as exc:
                errors += 1
                state.consecutive_errors += 1
                state.last_fetched_at = _utcnow()
                state.next_fetch_at = _utcnow() + timedelta(hours=min(2 ** state.consecutive_errors, 24))
                repository.record_error(run_id=run.id, platform_id=platform.id, collection_id=entry.collection_id, url=entry.url, stage=exc.stage, error=exc, retryable=exc.retryable)
                session.commit()
            except Exception as exc:
                errors += 1
                session.rollback()
                repository = CatalogRepository(
                    session,
                    object_store=object_store,
                    raw_retention_days=int(getattr(settings, "raw_retention_days", 365)),
                    review_threshold=float(getattr(settings, "classification_review_threshold", 0.80)),
                )
                run = session.get(CrawlRun, run_id)
                platform = session.get(Platform, platform_id)
                platform_run = session.scalar(select(PlatformRun).where(PlatformRun.run_id == run_id, PlatformRun.platform_id == platform_id))
                repository.record_error(run_id=run_id, platform_id=platform_id, collection_id=entry.collection_id, url=entry.url, stage="processing", error=exc, retryable=False)
                session.commit()

        expected_counts = [collection.expected_count for collection in collections if collection.expected_count is not None]
        observed = session.scalar(select(func.count(CatalogItem.id)).where(CatalogItem.platform_id == platform.id, CatalogItem.status == "active")) or 0
        collection_checks: list[bool] = []
        for collection in collections:
            collection_observed = session.scalar(
                select(func.count(CatalogItem.id)).where(
                    CatalogItem.collection_id == collection.id,
                    CatalogItem.status == "active",
                )
            ) or 0
            is_complete = (
                collection.expected_count is not None
                and collection_observed >= collection.expected_count
                and errors == 0
                and not hit_limit
            )
            collection.coverage_status = "complete" if is_complete else "partial"
            collection.last_run_at = _utcnow()
            if is_complete:
                collection.last_complete_at = _utcnow()
            collection_checks.append(is_complete)
        coverage_complete = bool(collection_checks) and all(collection_checks)
        retired = repository.reconcile_missing_items(run=run, platform=platform, coverage_complete=coverage_complete)
        platform_run.error_count = errors
        platform_run.observed_count = observed
        platform_run.expected_count = sum(expected_counts) if expected_counts else None
        platform_run.coverage_status = "complete" if coverage_complete else ("blocked" if root_blocked and fetched == 0 else "partial")
        if was_cancelled:
            platform_run.status = "cancelled"
        elif root_blocked and fetched == 0:
            platform_run.status = "blocked"
        elif fetched == 0:
            platform_run.status = "failed"
        elif errors or hit_limit:
            platform_run.status = "partial"
        else:
            platform_run.status = "success"
        platform_run.completeness_json = json_dumps({
            "coverage_complete": coverage_complete,
            "page_limit_hit": hit_limit,
            "frontier_remaining": len(frontier),
            "collections_with_expected_count": len(expected_counts),
            "collections_total": len(collections),
            "retired_after_three_complete_scans": retired,
        })
        if full:
            if platform.source_role == "reference":
                platform.onboarding_status = "out_of_scope"
            elif coverage_complete:
                platform.onboarding_status = "complete"
            elif root_blocked:
                platform.onboarding_status = "blocked"
            elif fetched == 0:
                platform.onboarding_status = "offline"
            else:
                platform.onboarding_status = "blocked"
                platform.notes = (platform.notes or "") + "；全量扫描未完成栏目对账，已保留运行证据"
        platform_run.finished_at = _utcnow()
        session.commit()
        return {
            "platform_id": platform.id,
            "name": platform.name,
            "status": platform_run.status,
            "coverage": platform_run.coverage_status,
            "pages": platform_run.pages_fetched,
            "items_seen": platform_run.items_seen,
            "items_new": platform_run.items_new,
            "items_updated": platform_run.items_updated,
            "errors": errors,
            "page_limit_hit": hit_limit,
            "cancelled": was_cancelled,
        }
    finally:
        session.close()


async def run_crawl(
    settings: object,
    platform_ids: list[str] | list[int] | None = None,
    full: bool = False,
    max_pages: int | None = None,
    trigger: str = "manual",
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run one crawl with concurrent platforms and a bounded global fetch pool."""

    logging.basicConfig(
        level=getattr(logging, str(getattr(settings, "log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    init_database(settings)
    factory = session_factory(settings)
    with session_scope(factory) as session:
        platform_count = session.scalar(select(func.count(Platform.id))) or 0
    if platform_count == 0:
        seed_platforms(settings)

    selected_ids = [int(value) for value in platform_ids] if platform_ids else None
    with session_scope(factory) as session:
        query = select(Platform.id).order_by(Platform.id)
        if selected_ids:
            query = query.where(Platform.id.in_(selected_ids))
        ids = list(session.scalars(query).all())
        repository = CatalogRepository(session)
        run = repository.create_run(
            mode="full" if full else "incremental",
            trigger=trigger,
            config={
                "platform_ids": ids,
                "max_pages_per_platform": max_pages or int(getattr(settings, "max_pages_per_platform", 1000)),
                "respect_robots_txt": bool(getattr(settings, "respect_robots_txt", True)),
                "verify_tls": bool(getattr(settings, "verify_tls", True)),
            },
        )
        run_id = run.id

    limit = max_pages or int(getattr(settings, "max_pages_per_platform", 1000))
    fetch_semaphore = asyncio.Semaphore(int(getattr(settings, "crawl_concurrency", 8)))
    platform_semaphore = asyncio.Semaphore(min(int(getattr(settings, "platform_concurrency", 4)), max(len(ids), 1)))
    renderer = BrowserRenderer(settings)
    if bool(getattr(settings, "enable_browser", True)):
        await renderer.start()
    try:
        object_store = build_object_store(settings)
    except Exception as exc:
        with session_scope(factory) as session:
            run = session.get(CrawlRun, run_id)
            if run is not None:
                CatalogRepository(session).finish_run(run, "failed", {"platforms": 0, "errors": 1}, str(exc))
                session.add(
                    Alert(
                        severity="error",
                        alert_type="object_store_unavailable",
                        title="原始对象存储不可用",
                        message=str(exc),
                        run_id=run_id,
                    )
                )
        await renderer.close()
        return {"run_id": run_id, "status": "failed", "stats": {"platforms": 0, "errors": 1}, "platforms": []}

    async with HttpFetcher(settings) as fetcher:
        async def guarded(platform_id: int) -> dict[str, Any]:
            async with platform_semaphore:
                return await _crawl_platform(
                    settings=settings,
                    factory=factory,
                    run_id=run_id,
                    platform_id=platform_id,
                    fetcher=fetcher,
                    renderer=renderer,
                    object_store=object_store,
                    fetch_semaphore=fetch_semaphore,
                    full=full,
                    max_pages=limit,
                    cancel_check=cancel_check,
                )

        raw_results = await asyncio.gather(*(guarded(platform_id) for platform_id in ids), return_exceptions=True)
    await renderer.close()

    results: list[dict[str, Any]] = []
    task_errors: list[str] = []
    for platform_id, result in zip(ids, raw_results, strict=True):
        if isinstance(result, BaseException):
            task_errors.append(f"platform {platform_id}: {type(result).__name__}: {result}")
            results.append({"platform_id": platform_id, "status": "failed", "errors": 1})
        else:
            results.append(result)
    statuses = [str(result.get("status")) for result in results]
    if any(status == "cancelled" for status in statuses):
        final_status = "cancelled"
    elif task_errors or any(status in {"failed", "blocked", "partial"} for status in statuses):
        final_status = "partial" if any(status in {"success", "no_url", "partial"} for status in statuses) else "failed"
    else:
        final_status = "success"
    stats = {
        "platforms": len(results),
        "success": sum(status == "success" for status in statuses),
        "partial": sum(status == "partial" for status in statuses),
        "blocked": sum(status == "blocked" for status in statuses),
        "failed": sum(status == "failed" for status in statuses),
        "no_url": sum(status == "no_url" for status in statuses),
        "pages": sum(int(result.get("pages", 0)) for result in results),
        "items_seen": sum(int(result.get("items_seen", 0)) for result in results),
        "items_new": sum(int(result.get("items_new", 0)) for result in results),
        "items_updated": sum(int(result.get("items_updated", 0)) for result in results),
        "errors": sum(int(result.get("errors", 0)) for result in results),
    }
    with session_scope(factory) as session:
        run = session.get(CrawlRun, run_id)
        if run is not None:
            CatalogRepository(session).finish_run(run, final_status, stats, "\n".join(task_errors))
            for result in results:
                if result.get("status") in {"failed", "blocked"}:
                    session.add(
                        Alert(
                            severity="error" if result.get("status") == "failed" else "warning",
                            alert_type="crawl_platform_failure",
                            title=f"平台采集{result.get('status')}",
                            message=str(result.get("name") or f"platform {result.get('platform_id')}"),
                            platform_id=int(result["platform_id"]),
                            run_id=run_id,
                        )
                    )
    return {"run_id": run_id, "status": final_status, "stats": stats, "platforms": results}
