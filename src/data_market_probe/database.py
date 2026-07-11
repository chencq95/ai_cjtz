"""Database lifecycle and session helpers."""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def _database_url(settings_or_url: object | str) -> str:
    if isinstance(settings_or_url, str):
        return settings_or_url
    value = getattr(settings_or_url, "database_url", None)
    if value is None:
        raise TypeError("settings must expose database_url")
    return str(value)


def create_db_engine(settings_or_url: object | str) -> Engine:
    url = _database_url(settings_or_url)
    if url.startswith("sqlite"):
        db_path = url.split("///", 1)[-1].split("?", 1)[0]
        if db_path and db_path != ":memory:":
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    return engine


@lru_cache(maxsize=8)
def _cached_session_factory(url: str) -> sessionmaker[Session]:
    return sessionmaker(
        bind=create_db_engine(url), expire_on_commit=False, autoflush=False
    )


def session_factory(settings_or_url: object | str) -> sessionmaker[Session]:
    return _cached_session_factory(_database_url(settings_or_url))


def init_database(settings: object, drop_existing: bool = False) -> Engine:
    engine = create_db_engine(settings)
    if drop_existing:
        Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
