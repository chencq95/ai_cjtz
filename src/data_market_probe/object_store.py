"""Raw response storage with filesystem, MinIO and database-compatible modes."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .utils import sha256_bytes


@dataclass(slots=True)
class StoredObject:
    key: str
    sha256: str
    size: int
    storage_tier: str


class ObjectStore(Protocol):
    backend: str

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...


class FilesystemObjectStore:
    backend = "filesystem"

    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self.root / key.replace("\\", "/")).resolve()
        if self.root not in candidate.parents and candidate != self.root:
            raise ValueError("object key escapes storage root")
        return candidate

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(data)
        return StoredObject(key=key, sha256=sha256_bytes(data), size=len(data), storage_tier=self.backend)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()


class MinioObjectStore:
    backend = "minio"

    def __init__(self, settings: object):
        from minio import Minio

        self.bucket = str(getattr(settings, "minio_bucket"))
        self.client = Minio(
            str(getattr(settings, "minio_endpoint")),
            access_key=str(getattr(settings, "minio_access_key")),
            secret_key=str(getattr(settings, "minio_secret_key")),
            secure=bool(getattr(settings, "minio_secure", False)),
        )
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def put(self, key: str, data: bytes, content_type: str) -> StoredObject:
        digest = sha256_bytes(data)
        self.client.put_object(
            self.bucket,
            key,
            io.BytesIO(data),
            len(data),
            content_type=content_type,
            metadata={"sha256": digest},
        )
        return StoredObject(key=key, sha256=digest, size=len(data), storage_tier=self.backend)

    def get(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def delete(self, key: str) -> None:
        self.client.remove_object(self.bucket, key)


def build_object_store(settings: object) -> ObjectStore | None:
    backend = str(getattr(settings, "object_store_backend", "filesystem"))
    if backend == "database":
        return None
    if backend == "minio":
        return MinioObjectStore(settings)
    return FilesystemObjectStore(getattr(settings, "object_store_path", Path("data/raw")))

