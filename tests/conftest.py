from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from data_market_probe.database import create_db_engine  # noqa: E402
from data_market_probe.models import Base  # noqa: E402


@pytest.fixture()
def db_session(tmp_path: Path) -> Iterator[Session]:
    """Return an isolated file-backed SQLite session for repository tests."""

    database_path = (tmp_path / "catalog-test.sqlite3").as_posix()
    engine = create_db_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )
    session = factory()
    try:
        yield session
        session.rollback()
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
