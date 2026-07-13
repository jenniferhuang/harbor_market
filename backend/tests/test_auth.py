from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner, URLSafeTimedSerializer
from sqlalchemy import select

from app.models import User


def test_register_creates_user_with_argon2_hash(
    client: TestClient,
    app: FastAPI,
) -> None:
    password = "correct horse battery staple"
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "  Alice  ", "password": password},
    )

    assert response.status_code == 201
    assert response.json()["data"]["username"] == "alice"
    assert "password" not in response.text
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "alice"))
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert password not in user.password_hash


def test_register_rejects_case_insensitive_duplicate(client: TestClient) -> None:
    first = client.post(
        "/api/v1/auth/register",
        json={"username": "Alice", "password": "correct horse battery staple"},
    )
    duplicate = client.post(
        "/api/v1/auth/register",
        json={"username": "ALICE", "password": "another sufficiently long password"},
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "error": {
            "code": "username_unavailable",
            "message": "That username is already registered",
        }
    }


def test_validation_error_is_field_level_and_never_echoes_password(client: TestClient) -> None:
    submitted_password = "short"
    response = client.post(
        "/api/v1/auth/register",
        json={"username": "not valid!", "password": submitted_password},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert {field["field"] for field in response.json()["error"]["fields"]} == {
        "username",
        "password",
    }
    assert response.json()["detail"] == response.json()["error"]["fields"]
    assert submitted_password not in response.text


def test_login_sets_cookie_updates_last_login_and_authenticates_me(
    client: TestClient,
    app: FastAPI,
    registered_user: dict[str, str],
) -> None:
    response = client.post("/api/v1/auth/login", json=registered_user)

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"]
    assert "harbor_market_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Secure" not in set_cookie
    assert "Max-Age=" in set_cookie
    assert response.json()["data"]["last_login_at"] is not None

    current_user = client.get("/api/v1/auth/me")
    assert current_user.status_code == 200
    assert current_user.json()["data"]["username"] == "alice"
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "alice"))
        assert user is not None
        assert user.last_login_at is not None


def test_login_failures_use_same_generic_response(
    client: TestClient,
    registered_user: dict[str, str],
) -> None:
    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"username": "alice", "password": "definitely wrong"},
    )
    unknown_user = client.post(
        "/api/v1/auth/login",
        json={"username": "unknown", "password": "definitely wrong"},
    )

    expected = {
        "error": {
            "code": "invalid_credentials",
            "message": "Invalid username or password",
        }
    }
    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json() == expected


def test_disabled_user_is_denied_with_generic_response(
    client: TestClient,
    app: FastAPI,
    registered_user: dict[str, str],
) -> None:
    with app.state.session_factory() as session:
        user = session.scalar(select(User).where(User.username == "alice"))
        assert user is not None
        user.is_active = False
        session.commit()

    response = client.post("/api/v1/auth/login", json=registered_user)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


@pytest.mark.parametrize("cookie_value", [None, "not-a-valid-signed-cookie"])
def test_me_rejects_missing_or_invalid_cookie(
    client: TestClient,
    cookie_value: str | None,
) -> None:
    if cookie_value is not None:
        client.cookies.set("harbor_market_session", cookie_value, path="/api/v1/auth")

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_me_rejects_expired_cookie(
    client: TestClient,
    app: FastAPI,
    registered_user: dict[str, str],
) -> None:
    login = client.post("/api/v1/auth/login", json=registered_user)
    user_id = login.json()["data"]["id"]

    class OldTimestampSigner(TimestampSigner):
        def get_timestamp(self) -> int:
            return 1

    settings = app.state.settings
    old_serializer = URLSafeTimedSerializer(
        settings.auth_secret_key.get_secret_value(),
        salt=settings.auth_signing_salt,
        signer=OldTimestampSigner,
        signer_kwargs={"digest_method": hashlib.sha256},
    )
    expired_token = old_serializer.dumps({"uid": user_id})
    client.cookies.set(
        settings.auth_cookie_name,
        expired_token,
        path=settings.auth_cookie_path,
    )

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_logout_expires_cookie_and_removes_access(
    client: TestClient,
    registered_user: dict[str, str],
) -> None:
    assert client.post("/api/v1/auth/login", json=registered_user).status_code == 200

    logout = client.post("/api/v1/auth/logout")

    assert logout.status_code == 200
    assert logout.json() == {"data": {"message": "Logged out"}}
    assert "Max-Age=0" in logout.headers["set-cookie"]
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/logout").status_code == 200


def test_failed_login_rate_limit_returns_retry_after(client: TestClient) -> None:
    credentials = {"username": "unknown", "password": "wrong password"}
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 401
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 401

    limited = client.post("/api/v1/auth/login", json=credentials)

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(limited.headers["retry-after"]) >= 1


def test_registration_rate_limit_is_enforced(client: TestClient, app: FastAPI) -> None:
    app.state.registration_limiter.limit = 1
    first = client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    limited = client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "password": "correct horse battery staple"},
    )

    assert first.status_code == 201
    assert limited.status_code == 429
