from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.dependencies import get_db


def test_health_checks_database(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok", "database": "ok", "storage": "disabled"}}


def test_health_checks_configured_object_storage(client: TestClient, app: FastAPI) -> None:
    app.state.settings.storage_backend = "minio"

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["storage"] == "ok"


def test_health_returns_503_when_object_storage_is_unavailable(
    client: TestClient,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.settings.storage_backend = "minio"

    def fail_storage_check() -> None:
        raise RuntimeError("offline")

    monkeypatch.setattr(app.state.object_storage, "ensure_bucket", fail_storage_check)
    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["error"] == {
        "code": "storage_unavailable",
        "message": "Object storage is unavailable",
    }


def test_api_sqlite_fixture_enforces_foreign_keys(app: FastAPI) -> None:
    with app.state.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1

    with pytest.raises(IntegrityError), app.state.engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO categories (code, name, parent_id) VALUES (?, ?, ?)",
            ("ORPHAN", "孤立类目", 999_999),
        )


def test_health_returns_503_when_database_is_unavailable(
    client: TestClient,
    app: FastAPI,
) -> None:
    class BrokenSession:
        def execute(self, _statement: object) -> None:
            raise OperationalError("SELECT 1", {}, Exception("offline"))

    def broken_database() -> Iterator[BrokenSession]:
        yield BrokenSession()

    app.dependency_overrides[get_db] = broken_database
    response = client.get("/api/v1/health")
    app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "database_unavailable",
            "message": "Database is unavailable",
        }
    }


def test_openapi_documents_required_endpoints_and_safe_user_shape(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    document = response.json()
    assert {
        "/api/v1/auth/register",
        "/api/v1/auth/login",
        "/api/v1/auth/logout",
        "/api/v1/auth/me",
        "/api/v1/health",
    }.issubset(document["paths"])
    assert set(document["components"]["schemas"]["UserPublic"]["properties"]) == {
        "id",
        "username",
        "is_admin",
        "created_at",
        "last_login_at",
    }


def test_auth_responses_have_security_headers(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")

    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_trusted_hosts_accept_configured_hostname_and_wildcard(client: TestClient) -> None:
    for host in ("app.hermes-node.com", "preview-123.trycloudflare.com"):
        response = client.get("/api/v1/health", headers={"Host": host})
        assert response.status_code == 200

    rejected = client.get("/api/v1/health", headers={"Host": "untrusted.example.com"})
    assert rejected.status_code == 400
