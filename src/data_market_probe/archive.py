"""Move expired online raw objects to a checksum-preserving archive volume."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from .database import session_factory, session_scope
from .models import Alert, PageSnapshot
from .object_store import FilesystemObjectStore, build_object_store
from .utils import sha256_bytes


def archive_expired_snapshots(settings: object, limit: int = 500) -> dict[str, int]:
    source = build_object_store(settings)
    if source is None:
        return {"archived": 0, "failed": 0}
    archive = FilesystemObjectStore(Path(getattr(settings, "archive_path", "data/archive")))
    factory = session_factory(settings)
    archived = failed = 0
    now = datetime.now(timezone.utc)
    with session_scope(factory) as session:
        snapshots = session.scalars(
            select(PageSnapshot)
            .where(
                PageSnapshot.object_key != "",
                PageSnapshot.archived_at.is_(None),
                PageSnapshot.raw_expires_at.is_not(None),
                PageSnapshot.raw_expires_at <= now,
            )
            .limit(limit)
        ).all()
        for snapshot in snapshots:
            try:
                data = source.get(snapshot.object_key)
                if sha256_bytes(data) != snapshot.object_sha256:
                    raise ValueError("raw object checksum mismatch")
                target_key = f"archive/{snapshot.object_key}"
                stored = archive.put(target_key, data, "application/gzip")
                if stored.sha256 != snapshot.object_sha256:
                    raise ValueError("archive checksum mismatch")
                source.delete(snapshot.object_key)
                snapshot.object_key = target_key
                snapshot.storage_tier = "archive"
                snapshot.archived_at = now
                archived += 1
            except Exception as exc:
                failed += 1
                session.add(
                    Alert(
                        severity="error",
                        alert_type="archive_failed",
                        title="原始快照归档失败",
                        message=f"snapshot={snapshot.id}: {exc}",
                    )
                )
    return {"archived": archived, "failed": failed}

