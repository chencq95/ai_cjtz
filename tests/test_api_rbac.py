from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def test_api_login_sources_and_readonly_rbac(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DMP_DATABASE_URL", f"sqlite:///{(tmp_path / 'api.db').as_posix()}")
    monkeypatch.setenv("DMP_OBJECT_STORE_BACKEND", "filesystem")
    monkeypatch.setenv("DMP_OBJECT_STORE_PATH", str(tmp_path / "raw"))
    monkeypatch.setenv("DMP_AUTH_SECRET_KEY", "test-secret-key-with-at-least-32-characters")
    monkeypatch.setenv("DMP_BOOTSTRAP_ADMIN_PASSWORD", "AdminPassword123!")
    from data_market_probe.settings import get_settings

    get_settings.cache_clear()
    import data_market_probe.api as api_module

    api_module = importlib.reload(api_module)
    with TestClient(api_module.app) as client:
        assert client.get("/api/v1/platforms").status_code == 401
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "AdminPassword123!"})
        assert login.status_code == 200
        platforms = client.get("/api/v1/platforms")
        assert platforms.status_code == 200
        assert len(platforms.json()) == 38
        create = client.post("/api/v1/users", json={"username": "viewer", "password": "ViewerPassword123!", "role": "readonly"})
        assert create.status_code == 200
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"username": "viewer", "password": "ViewerPassword123!"}).status_code == 200
        assert client.get("/api/v1/catalog/items").status_code == 200
        assert client.patch("/api/v1/platforms/1", json={"enabled": False}).status_code == 403
    get_settings.cache_clear()

