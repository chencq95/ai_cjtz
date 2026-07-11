from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from data_market_probe.bootstrap import ensure_defaults
from data_market_probe.database import session_factory, session_scope
from data_market_probe.models import CrawlSchedule, Platform, SourceCollection, User
from data_market_probe.object_store import FilesystemObjectStore
from data_market_probe.settings import Settings


def test_filesystem_object_store_round_trip(tmp_path: Path) -> None:
    store = FilesystemObjectStore(tmp_path / "objects")
    stored = store.put("platform/1/test.gz", b"compressed", "application/gzip")
    assert stored.sha256
    assert store.get(stored.key) == b"compressed"
    store.delete(stored.key)
    assert not (tmp_path / "objects/platform/1/test.gz").exists()


def test_bootstrap_creates_38_sources_roles_collections_and_schedules(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{(tmp_path / 'bootstrap.db').as_posix()}",
        object_store_backend="filesystem",
        object_store_path=tmp_path / "raw",
        auth_secret_key="test-secret-key-with-at-least-32-characters",
    )
    result = ensure_defaults(settings)
    assert result["platforms"] == 38
    factory = session_factory(settings)
    with session_scope(factory) as session:
        assert session.scalar(select(func.count(Platform.id))) == 38
        assert session.get(Platform, 38).source_role == "reference"
        assert session.get(Platform, 32).onboarding_status == "blocked"
        assert session.scalar(select(func.count(CrawlSchedule.id))) == 2
        assert session.scalar(select(func.count(User.id))) == 1
        hubei = session.get(Platform, 8)
        assert hubei.adapter == "hubei-public-v1"
        enabled_hubei = session.scalar(
            select(func.count(SourceCollection.id)).where(
                SourceCollection.platform_id == 8,
                SourceCollection.enabled.is_(True),
            )
        )
        assert enabled_hubei >= 5

