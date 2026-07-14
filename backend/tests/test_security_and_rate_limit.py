from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.rate_limit import SlidingWindowRateLimiter


def test_production_cookie_defaults_to_secure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_COOKIE_SECURE")
    settings = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql+psycopg://user@db/xiangyue_xiamen",
        auth_secret_key="a-production-signing-key-with-at-least-32-characters",
    )

    assert settings.auth_cookie_secure is True


def test_production_rejects_insecure_cookie() -> None:
    with pytest.raises(ValidationError, match="production requires AUTH_COOKIE_SECURE=true"):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://user@db/xiangyue_xiamen",
            auth_secret_key="a-production-signing-key-with-at-least-32-characters",
            auth_cookie_secure=False,
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"auth_secret_key": "replace-with-at-least-32-random-bytes"},
            "AUTH_SECRET_KEY",
        ),
        (
            {
                "database_url": (
                    "postgresql+psycopg://harbor_market:"
                    "replace-with-a-generated-password@db/xiangyue_xiamen"
                )
            },
            "DATABASE_URL",
        ),
        (
            {
                "storage_backend": "minio",
                "storage_access_key": "replace-with-a-random-app-access-key",
                "storage_secret_key": "valid-storage-secret-value",
            },
            "STORAGE_ACCESS_KEY",
        ),
        (
            {
                "storage_backend": "minio",
                "storage_access_key": "valid-storage-access-key",
                "storage_secret_key": "replace-with-a-different-32-character-secret",
            },
            "STORAGE_SECRET_KEY",
        ),
    ],
)
def test_production_rejects_committed_placeholder_credentials(
    overrides: dict[str, str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in (
        "ENVIRONMENT",
        "DATABASE_URL",
        "AUTH_SECRET",
        "AUTH_SECRET_KEY",
        "AUTH_COOKIE_SECURE",
    ):
        monkeypatch.delenv(key, raising=False)
    values = {
        "environment": "production",
        "database_url": "postgresql+psycopg://user@db/xiangyue_xiamen",
        "auth_secret_key": "a-production-signing-key-with-at-least-32-characters",
        "auth_cookie_secure": True,
        **overrides,
    }
    with pytest.raises(ValidationError, match=message):
        Settings(_env_file=None, **values)


def test_compose_setting_aliases_are_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_SECRET_KEY")
    monkeypatch.setenv("AUTH_SECRET", "compose-signing-secret-that-is-at-least-32-characters")
    monkeypatch.setenv("AUTH_TOKEN_TTL_MINUTES", "15")
    monkeypatch.setenv("ALLOWED_HOSTS", "app.example.test,localhost")

    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://user@db/xiangyue_xiamen",
        auth_cookie_secure=False,
    )

    assert settings.auth_secret_key.get_secret_value().startswith("compose-signing")
    assert settings.auth_session_ttl_seconds == 900
    assert settings.parsed_allowed_hosts == ["app.example.test", "localhost"]


def test_sliding_window_reopens_after_window() -> None:
    now = 100.0
    limiter = SlidingWindowRateLimiter(2, 10, max_keys=100, clock=lambda: now)

    assert limiter.consume("client") is None
    assert limiter.consume("client") is None
    assert limiter.consume("client") == 10
    now = 111.0
    assert limiter.check("client") is None
    assert limiter.consume("client") is None
