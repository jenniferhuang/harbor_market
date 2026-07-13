from __future__ import annotations

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.api.dependencies import get_db


def test_health_checks_database(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"data": {"status": "ok", "database": "ok"}}


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
        "created_at",
        "last_login_at",
    }


def test_auth_responses_have_security_headers(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")

    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_trusted_hosts_accept_configured_hostname_and_wildcard(client: TestClient) -> None:
    for host in ("app.hermes-node.com", "preview-123.trycloudflare.com"):
        response = client.get("/api/v1/health", headers={"Host": host})
        assert response.status_code == 200

    rejected = client.get("/api/v1/health", headers={"Host": "untrusted.example.com"})
    assert rejected.status_code == 400
