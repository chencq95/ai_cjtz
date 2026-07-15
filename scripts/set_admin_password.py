"""Interactively update the admin password without putting it in source or logs."""

from __future__ import annotations

import getpass

from sqlalchemy import select

from data_market_probe.auth import hash_password
from data_market_probe.database import session_factory, session_scope
from data_market_probe.models import User
from data_market_probe.settings import get_settings


def main() -> None:
    first = getpass.getpass("New admin password (6+ chars): ")
    second = getpass.getpass("Repeat new admin password: ")
    if len(first) < 6:
        raise SystemExit("password must contain at least 6 characters")
    if first != second:
        raise SystemExit("passwords do not match")
    settings = get_settings()
    with session_scope(session_factory(settings)) as session:
        user = session.scalar(select(User).where(User.username == "admin"))
        if user is None:
            raise SystemExit("admin user not found")
        user.password_hash = hash_password(first)
        user.must_change_password = False
    print("admin password updated")


if __name__ == "__main__":
    main()
