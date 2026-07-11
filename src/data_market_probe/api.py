"""FastAPI management and read-only catalog service for the operations console."""

from __future__ import annotations

import asyncio
import gzip
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from croniter import croniter
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from .auth import (
    COOKIE_NAME,
    create_access_token,
    get_current_user,
    get_db,
    hash_password,
    require_admin,
    verify_password,
    write_audit,
)
from .bootstrap import ensure_defaults
from .database import session_factory, session_scope
from .fetching import HttpFetcher, domain_family
from .models import (
    Alert,
    AuditLog,
    ChangeEvent,
    ClassificationReview,
    CrawlError,
    CrawlRun,
    CrawlSchedule,
    CrawlTask,
    Platform,
    PlatformRun,
    RunLog,
    SourceCollection,
    TaxonomyMapping,
    User,
    PageSnapshot,
)
from .object_store import FilesystemObjectStore, build_object_store
from .query import (
    catalog_facets,
    coverage_matrix,
    dashboard,
    get_item,
    item_versions,
    list_collections,
    list_platforms,
    search_catalog,
)
from .scheduler import _next_run
from .settings import Settings, get_settings
from .tasks import dispatch_crawl, request_cancel
from .utils import canonicalize_url, json_dumps, registrable_host


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (json.JSONDecodeError, TypeError):
        return fallback


class LoginRequest(BaseModel):
    username: str
    password: str


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=256)


class PlatformUpdate(BaseModel):
    name: str | None = None
    canonical_url: str | None = None
    enabled: bool | None = None
    render_mode: Literal["auto", "http", "browser"] | None = None
    adapter: str | None = None
    onboarding_status: str | None = None
    legal_review_status: str | None = None
    default_rate_limit: float | None = Field(default=None, gt=0, le=100)
    max_concurrency: int | None = Field(default=None, ge=1, le=32)
    notes: str | None = None


class CollectionUpdate(BaseModel):
    name: str | None = None
    entry_url: str | None = None
    enabled: bool | None = None
    adapter: str | None = None
    adapter_version: str | None = None
    pagination_mode: str | None = None
    expected_count: int | None = Field(default=None, ge=0)
    coverage_status: str | None = None
    notes: str | None = None


class ScheduleInput(BaseModel):
    name: str
    cron_expression: str
    timezone: str = "Asia/Shanghai"
    mode: Literal["incremental", "full"] = "incremental"
    platform_ids: list[int] = []
    enabled: bool = True
    max_pages: int | None = Field(default=None, ge=1)


class TriggerInput(BaseModel):
    mode: Literal["incremental", "full"] = "incremental"
    platform_ids: list[int] = []
    max_pages: int | None = Field(default=None, ge=1)


class ReviewDecision(BaseModel):
    decision: Literal["accepted", "rejected"]
    value: str | None = None
    note: str = ""


class MappingInput(BaseModel):
    dimension_type: str
    raw_value: str
    normalized_value: str
    confidence: float = Field(default=1.0, ge=0, le=1)
    enabled: bool = True


class UserInput(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=10, max_length=256)
    role: Literal["admin", "readonly"] = "readonly"


class UserUpdate(BaseModel):
    role: Literal["admin", "readonly"] | None = None
    enabled: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=256)


def _task_payload(row: CrawlTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "celery_task_id": row.celery_task_id,
        "mode": row.mode,
        "platform_ids": _json(row.platform_ids_json, []),
        "max_pages": row.max_pages,
        "status": row.status,
        "requested_by": row.requested_by,
        "created_at": _iso(row.created_at),
        "started_at": _iso(row.started_at),
        "finished_at": _iso(row.finished_at),
        "cancel_requested": row.cancel_requested,
        "error": row.error_message,
    }


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        ensure_defaults(settings)
        yield

    app = FastAPI(
        title="全国数据交易所爬虫运维平台",
        version="1.0.0",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def health(session: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
        session.scalar(select(func.count(Platform.id)))
        return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "version": app.version}

    @app.post("/api/v1/auth/login")
    def login(payload: LoginRequest, response: Response, request: Request, session: Annotated[Session, Depends(get_db)]) -> dict[str, Any]:
        user = session.scalar(select(User).where(User.username == payload.username))
        if user is None or not user.enabled or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
        user.last_login_at = datetime.now(timezone.utc)
        token = create_access_token(user, settings)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="strict",
            max_age=settings.auth_token_minutes * 60,
            path="/",
        )
        write_audit(session, request, user, "auth.login", "user", user.id)
        session.commit()
        return {"id": user.id, "username": user.username, "role": user.role, "must_change_password": user.must_change_password}

    @app.post("/api/v1/auth/logout")
    def logout(response: Response, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]) -> dict[str, bool]:
        response.delete_cookie(COOKIE_NAME, path="/")
        write_audit(session, request, user, "auth.logout", "user", user.id)
        session.commit()
        return {"ok": True}

    @app.get("/api/v1/auth/me")
    def me(user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
        return {"id": user.id, "username": user.username, "role": user.role, "must_change_password": user.must_change_password}

    @app.post("/api/v1/auth/change-password")
    def change_password(payload: PasswordChange, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(get_current_user)]) -> dict[str, bool]:
        if not verify_password(payload.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="当前密码不正确")
        user.password_hash = hash_password(payload.new_password)
        user.must_change_password = False
        write_audit(session, request, user, "auth.change_password", "user", user.id)
        session.commit()
        return {"ok": True}

    @app.get("/api/v1/dashboard")
    def dashboard_route(session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
        return dashboard(session)

    @app.get("/api/v1/platforms")
    def platforms_route(session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> list[dict[str, Any]]:
        return list_platforms(session)

    @app.patch("/api/v1/platforms/{platform_id}")
    def update_platform(platform_id: int, payload: PlatformUpdate, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        platform = session.get(Platform, platform_id)
        if platform is None:
            raise HTTPException(status_code=404, detail="平台不存在")
        changes = payload.model_dump(exclude_unset=True)
        if "canonical_url" in changes and changes["canonical_url"]:
            canonical = canonicalize_url(changes["canonical_url"])
            if not canonical:
                raise HTTPException(status_code=422, detail="网址必须是有效的 HTTP(S) 地址")
            changes["canonical_url"] = canonical
        for key, value in changes.items():
            setattr(platform, key, value)
        write_audit(session, request, user, "platform.update", "platform", str(platform_id), changes)
        session.commit()
        return next(item for item in list_platforms(session) if item["id"] == platform_id)

    @app.post("/api/v1/platforms/{platform_id}/check")
    async def check_platform(platform_id: int, session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        platform = session.get(Platform, platform_id)
        if platform is None or not (platform.canonical_url or platform.source_url):
            raise HTTPException(status_code=404, detail="平台或网址不存在")
        url = canonicalize_url(platform.canonical_url or platform.source_url)
        families = {domain_family(registrable_host(url))}
        async with HttpFetcher(settings) as fetcher:
            try:
                result = await fetcher.fetch(url, allowed_families=families)
                return {"ok": result.status_code < 400, "status_code": result.status_code, "final_url": result.final_url, "mime_type": result.mime_type}
            except Exception as exc:
                return {"ok": False, "error": str(exc)}

    @app.get("/api/v1/collections")
    def collections_route(platform_id: int | None = None, session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(get_current_user)] = None) -> list[dict[str, Any]]:
        return list_collections(session, platform_id)

    @app.patch("/api/v1/collections/{collection_id}")
    def update_collection(collection_id: int, payload: CollectionUpdate, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        row = session.get(SourceCollection, collection_id)
        if row is None:
            raise HTTPException(status_code=404, detail="栏目不存在")
        changes = payload.model_dump(exclude_unset=True)
        if changes.get("entry_url"):
            changes["entry_url"] = canonicalize_url(changes["entry_url"])
        for key, value in changes.items():
            setattr(row, key, value)
        write_audit(session, request, user, "collection.update", "collection", str(collection_id), changes)
        session.commit()
        return next(item for item in list_collections(session) if item["id"] == collection_id)

    @app.get("/api/v1/schedules")
    def schedules_route(session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> list[dict[str, Any]]:
        return [{"id": row.id, "name": row.name, "cron_expression": row.cron_expression, "timezone": row.timezone, "mode": row.mode, "platform_ids": _json(row.platform_ids_json, []), "enabled": row.enabled, "max_pages": row.max_pages, "last_run_at": _iso(row.last_run_at), "next_run_at": _iso(row.next_run_at)} for row in session.scalars(select(CrawlSchedule).order_by(CrawlSchedule.id)).all()]

    @app.post("/api/v1/schedules")
    def create_schedule(payload: ScheduleInput, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        if not croniter.is_valid(payload.cron_expression):
            raise HTTPException(status_code=422, detail="Cron 表达式无效")
        row = CrawlSchedule(name=payload.name, cron_expression=payload.cron_expression, timezone=payload.timezone, mode=payload.mode, platform_ids_json=json_dumps(payload.platform_ids), enabled=payload.enabled, max_pages=payload.max_pages, next_run_at=_next_run(payload.cron_expression, payload.timezone))
        session.add(row)
        session.flush()
        write_audit(session, request, user, "schedule.create", "schedule", str(row.id), payload.model_dump())
        session.commit()
        return {"id": row.id, "next_run_at": _iso(row.next_run_at)}

    @app.patch("/api/v1/schedules/{schedule_id}")
    def update_schedule(schedule_id: int, payload: ScheduleInput, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        row = session.get(CrawlSchedule, schedule_id)
        if row is None:
            raise HTTPException(status_code=404, detail="计划不存在")
        if not croniter.is_valid(payload.cron_expression):
            raise HTTPException(status_code=422, detail="Cron 表达式无效")
        row.name = payload.name
        row.cron_expression = payload.cron_expression
        row.timezone = payload.timezone
        row.mode = payload.mode
        row.platform_ids_json = json_dumps(payload.platform_ids)
        row.enabled = payload.enabled
        row.max_pages = payload.max_pages
        row.next_run_at = _next_run(payload.cron_expression, payload.timezone)
        write_audit(session, request, user, "schedule.update", "schedule", str(row.id), payload.model_dump())
        session.commit()
        return {"id": row.id, "next_run_at": _iso(row.next_run_at)}

    @app.delete("/api/v1/schedules/{schedule_id}")
    def delete_schedule(schedule_id: int, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, bool]:
        row = session.get(CrawlSchedule, schedule_id)
        if row is None:
            raise HTTPException(status_code=404, detail="计划不存在")
        session.delete(row)
        write_audit(session, request, user, "schedule.delete", "schedule", str(schedule_id))
        session.commit()
        return {"ok": True}

    @app.post("/api/v1/tasks")
    def trigger_task(payload: TriggerInput, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        task = dispatch_crawl(settings, mode=payload.mode, platform_ids=payload.platform_ids or None, max_pages=payload.max_pages, requested_by=user.username)
        write_audit(session, request, user, "crawl.trigger", "task", task.id, payload.model_dump())
        session.commit()
        return _task_payload(task)

    @app.get("/api/v1/tasks")
    def tasks_route(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=200), session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(get_current_user)] = None) -> dict[str, Any]:
        total = session.scalar(select(func.count(CrawlTask.id))) or 0
        rows = session.scalars(select(CrawlTask).order_by(CrawlTask.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [_task_payload(row) for row in rows], "total": total, "page": page, "page_size": page_size}

    @app.post("/api/v1/tasks/{task_id}/cancel")
    def cancel_task(task_id: str, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, bool]:
        ok = request_cancel(settings, task_id)
        if not ok:
            raise HTTPException(status_code=409, detail="任务不可取消")
        write_audit(session, request, user, "crawl.cancel", "task", task_id)
        session.commit()
        return {"ok": True}

    @app.post("/api/v1/tasks/{task_id}/retry")
    def retry_task(task_id: str, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        old = session.get(CrawlTask, task_id)
        if old is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        task = dispatch_crawl(settings, mode=old.mode, platform_ids=_json(old.platform_ids_json, []) or None, max_pages=old.max_pages, requested_by=user.username)
        write_audit(session, request, user, "crawl.retry", "task", task.id, {"source_task_id": task_id})
        session.commit()
        return _task_payload(task)

    @app.get("/api/v1/runs")
    def runs_route(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=200), session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(get_current_user)] = None) -> dict[str, Any]:
        total = session.scalar(select(func.count(CrawlRun.id))) or 0
        rows = session.scalars(select(CrawlRun).order_by(CrawlRun.started_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [{"id": row.id, "mode": row.mode, "trigger": row.trigger, "status": row.status, "started_at": _iso(row.started_at), "finished_at": _iso(row.finished_at), "stats": _json(row.stats_json, {}), "error_summary": row.error_summary} for row in rows], "total": total, "page": page, "page_size": page_size}

    @app.get("/api/v1/runs/{run_id}")
    def run_detail(run_id: str, session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
        run = session.get(CrawlRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="运行不存在")
        platforms = session.scalars(select(PlatformRun).where(PlatformRun.run_id == run_id).order_by(PlatformRun.platform_id)).all()
        errors = session.scalars(select(CrawlError).where(CrawlError.run_id == run_id).order_by(CrawlError.id.desc()).limit(500)).all()
        return {"run": {"id": run.id, "mode": run.mode, "status": run.status, "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at), "stats": _json(run.stats_json, {})}, "platforms": [{"platform_id": row.platform_id, "status": row.status, "coverage": row.coverage_status, "pages": row.pages_fetched, "items": row.items_seen, "errors": row.error_count, "completeness": _json(row.completeness_json, {})} for row in platforms], "errors": [{"id": row.id, "platform_id": row.platform_id, "url": row.url, "stage": row.stage, "type": row.error_type, "message": row.message, "retryable": row.retryable, "created_at": _iso(row.created_at)} for row in errors]}

    @app.get("/api/v1/tasks/{task_id}/logs")
    async def task_logs(task_id: str, _user: Annotated[User, Depends(get_current_user)]) -> EventSourceResponse:
        factory = session_factory(settings)

        async def events():
            cursor = 0
            idle_after_done = 0
            while True:
                with session_scope(factory) as session:
                    logs = session.scalars(select(RunLog).where(RunLog.task_id == task_id, RunLog.id > cursor).order_by(RunLog.id)).all()
                    task = session.get(CrawlTask, task_id)
                    for row in logs:
                        cursor = row.id
                        yield {"id": str(row.id), "event": "log", "data": json_dumps({"level": row.level, "message": row.message, "created_at": _iso(row.created_at)})}
                    done = task is None or task.status in {"success", "partial", "failed", "cancelled"}
                if done:
                    idle_after_done += 1
                    if idle_after_done >= 2:
                        yield {"event": "end", "data": json_dumps({"status": task.status if task else "missing"})}
                        break
                await asyncio.sleep(1)

        return EventSourceResponse(events())

    @app.get("/api/v1/catalog/items")
    def catalog_route(q: str | None = None, kind: str | None = None, product_type: str | None = None, industry: str | None = None, region: str | None = None, provider: str | None = None, platform_id: int | None = None, item_status: str = "active", published_from: datetime | None = None, published_to: datetime | None = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(get_current_user)] = None) -> dict[str, Any]:
        return search_catalog(session, q=q, kind=kind, product_type=product_type, industry=industry, region=region, provider=provider, platform_id=platform_id, status=item_status, published_from=published_from, published_to=published_to, page=page, page_size=page_size)

    @app.get("/api/v1/catalog/facets")
    def facets_route(session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
        return catalog_facets(session)

    @app.get("/api/v1/catalog/items/{item_id}")
    def item_route(item_id: str, session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> dict[str, Any]:
        result = get_item(session, item_id)
        if result is None:
            raise HTTPException(status_code=404, detail="条目不存在")
        return result

    @app.get("/api/v1/catalog/items/{item_id}/versions")
    def versions_route(item_id: str, session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> list[dict[str, Any]]:
        return item_versions(session, item_id)

    @app.get("/api/v1/coverage")
    def coverage_route(session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> list[dict[str, Any]]:
        return coverage_matrix(session)

    @app.get("/api/v1/changes")
    def changes_route(after_id: int = 0, limit: int = Query(100, ge=1, le=1000), session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(get_current_user)] = None) -> list[dict[str, Any]]:
        rows = session.scalars(select(ChangeEvent).where(ChangeEvent.id > after_id).order_by(ChangeEvent.id).limit(limit)).all()
        return [{"id": row.id, "platform_id": row.platform_id, "item_id": row.item_id, "version_id": row.version_id, "event_type": row.event_type, "created_at": _iso(row.created_at), "payload": _json(row.payload_json, {})} for row in rows]

    @app.get("/api/v1/reviews")
    def reviews_route(review_status: str = "pending", session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(get_current_user)] = None) -> list[dict[str, Any]]:
        rows = session.scalars(select(ClassificationReview).where(ClassificationReview.status == review_status).order_by(ClassificationReview.created_at.desc()).limit(1000)).all()
        return [{"id": row.id, "item_id": row.item_id, "version_id": row.version_id, "field": row.field_name, "proposed_value": row.proposed_value, "confidence": row.confidence, "status": row.status, "reviewer": row.reviewer, "created_at": _iso(row.created_at)} for row in rows]

    @app.patch("/api/v1/reviews/{review_id}")
    def decide_review(review_id: int, payload: ReviewDecision, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        row = session.get(ClassificationReview, review_id)
        if row is None:
            raise HTTPException(status_code=404, detail="审核记录不存在")
        row.status = payload.decision
        row.reviewer = user.username
        row.reviewed_at = datetime.now(timezone.utc)
        row.decision_note = payload.note
        if payload.decision == "accepted" and payload.value is not None:
            from .models import CatalogItemVersion

            version = session.get(CatalogItemVersion, row.version_id)
            if version and row.field_name == "product_type":
                version.product_type = payload.value
                dimensions = session.scalars(
                    select(ItemDimension).where(
                        ItemDimension.version_id == row.version_id,
                        ItemDimension.dimension_type == "product_type",
                    )
                ).all()
                for dimension in dimensions:
                    dimension.normalized_value = payload.value
                    dimension.confidence = 1.0
                    dimension.method = "manual_review"
        write_audit(session, request, user, "review.decide", "classification_review", str(review_id), payload.model_dump())
        session.commit()
        return {"id": row.id, "status": row.status}

    @app.get("/api/v1/mappings")
    def mappings_route(session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> list[dict[str, Any]]:
        rows = session.scalars(select(TaxonomyMapping).order_by(TaxonomyMapping.dimension_type, TaxonomyMapping.raw_value)).all()
        return [{"id": row.id, "dimension_type": row.dimension_type, "raw_value": row.raw_value, "normalized_value": row.normalized_value, "confidence": row.confidence, "enabled": row.enabled} for row in rows]

    @app.post("/api/v1/mappings")
    def create_mapping(payload: MappingInput, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        row = TaxonomyMapping(**payload.model_dump())
        session.add(row)
        session.flush()
        write_audit(session, request, user, "mapping.create", "taxonomy_mapping", str(row.id), payload.model_dump())
        session.commit()
        return {"id": row.id}

    @app.get("/api/v1/alerts")
    def alerts_route(alert_status: str = "open", session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(get_current_user)] = None) -> list[dict[str, Any]]:
        rows = session.scalars(select(Alert).where(Alert.status == alert_status).order_by(Alert.created_at.desc()).limit(1000)).all()
        return [{"id": row.id, "severity": row.severity, "type": row.alert_type, "title": row.title, "message": row.message, "platform_id": row.platform_id, "run_id": row.run_id, "status": row.status, "created_at": _iso(row.created_at)} for row in rows]

    @app.post("/api/v1/alerts/{alert_id}/ack")
    def acknowledge_alert(alert_id: int, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, bool]:
        row = session.get(Alert, alert_id)
        if row is None:
            raise HTTPException(status_code=404, detail="告警不存在")
        row.status = "acknowledged"
        row.acknowledged_by = user.username
        row.acknowledged_at = datetime.now(timezone.utc)
        write_audit(session, request, user, "alert.acknowledge", "alert", str(alert_id))
        session.commit()
        return {"ok": True}

    @app.get("/api/v1/users")
    def users_route(session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(require_admin)]) -> list[dict[str, Any]]:
        rows = session.scalars(select(User).order_by(User.username)).all()
        return [{"id": row.id, "username": row.username, "role": row.role, "enabled": row.enabled, "must_change_password": row.must_change_password, "last_login_at": _iso(row.last_login_at), "created_at": _iso(row.created_at)} for row in rows]

    @app.post("/api/v1/users")
    def create_user(payload: UserInput, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        if session.scalar(select(User).where(User.username == payload.username)):
            raise HTTPException(status_code=409, detail="用户名已存在")
        row = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role, must_change_password=True)
        session.add(row)
        session.flush()
        write_audit(session, request, user, "user.create", "user", row.id, {"username": row.username, "role": row.role})
        session.commit()
        return {"id": row.id, "username": row.username, "role": row.role}

    @app.patch("/api/v1/users/{user_id}")
    def update_user(user_id: str, payload: UserUpdate, request: Request, session: Annotated[Session, Depends(get_db)], user: Annotated[User, Depends(require_admin)]) -> dict[str, Any]:
        row = session.get(User, user_id)
        if row is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        changes = payload.model_dump(exclude_unset=True)
        password = changes.pop("password", None)
        for key, value in changes.items():
            setattr(row, key, value)
        if password:
            row.password_hash = hash_password(password)
            row.must_change_password = True
        write_audit(session, request, user, "user.update", "user", user_id, {key: value for key, value in changes.items()})
        session.commit()
        return {"id": row.id, "username": row.username, "role": row.role, "enabled": row.enabled}

    @app.get("/api/v1/audit")
    def audit_route(page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), session: Annotated[Session, Depends(get_db)] = None, _user: Annotated[User, Depends(require_admin)] = None) -> dict[str, Any]:
        total = session.scalar(select(func.count(AuditLog.id))) or 0
        rows = session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all()
        return {"items": [{"id": row.id, "username": row.username, "action": row.action, "resource_type": row.resource_type, "resource_id": row.resource_id, "detail": _json(row.detail_json, {}), "ip_address": row.ip_address, "created_at": _iso(row.created_at)} for row in rows], "total": total}

    @app.get("/api/v1/snapshots/{snapshot_id}/content")
    def snapshot_content(snapshot_id: int, session: Annotated[Session, Depends(get_db)], _user: Annotated[User, Depends(get_current_user)]) -> StreamingResponse:
        snapshot = session.get(PageSnapshot, snapshot_id)
        if snapshot is None:
            raise HTTPException(status_code=404, detail="快照不存在")
        if snapshot.raw_body_gzip is not None:
            compressed = snapshot.raw_body_gzip
        elif snapshot.object_key:
            store = FilesystemObjectStore(settings.archive_path) if snapshot.storage_tier == "archive" else build_object_store(settings)
            if store is None:
                raise HTTPException(status_code=503, detail="对象存储不可用")
            compressed = store.get(snapshot.object_key)
        else:
            raise HTTPException(status_code=410, detail="原始内容已归档或不存在")
        data = gzip.decompress(compressed)
        return StreamingResponse(iter([data]), media_type="application/octet-stream", headers={"Content-Disposition": f'attachment; filename="snapshot-{snapshot_id}.bin"', "X-Content-Type-Options": "nosniff"})

    return app


app = create_app()
