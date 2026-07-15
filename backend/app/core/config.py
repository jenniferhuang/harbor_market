from __future__ import annotations

import re
from functools import lru_cache
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Harbor Market API"
    environment: Literal["development", "test", "production"] = "production"
    database_url: SecretStr
    database_connect_timeout_seconds: int = Field(default=5, ge=1, le=60)
    database_pool_size: int = Field(default=5, ge=1, le=50)
    database_max_overflow: int = Field(default=5, ge=0, le=100)

    auth_secret_key: SecretStr = Field(
        min_length=32,
        validation_alias=AliasChoices("AUTH_SECRET_KEY", "AUTH_SECRET"),
    )
    auth_cookie_name: str = "harbor_market_session"
    auth_cookie_path: str = "/api/v1"
    auth_cookie_domain: str | None = None
    auth_cookie_secure: bool = True
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_session_ttl_seconds: int = Field(default=8 * 60 * 60, ge=1, le=30 * 24 * 60 * 60)
    auth_token_ttl_minutes: int | None = Field(default=None, ge=1, le=30 * 24 * 60)
    auth_signing_salt: str = "harbor-market-auth-v1"

    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost_kib: int = Field(default=65_536, ge=8_192, le=262_144)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)

    registration_rate_limit: int = Field(default=5, ge=1, le=1_000)
    registration_rate_window_seconds: int = Field(default=60, ge=1, le=86_400)
    login_failure_rate_limit: int = Field(default=5, ge=1, le=1_000)
    login_failure_rate_window_seconds: int = Field(default=60, ge=1, le=86_400)
    rate_limit_max_keys: int = Field(default=10_000, ge=100, le=1_000_000)

    cors_allowed_origins: str = ""
    allowed_hosts: str = "localhost,127.0.0.1"
    trust_proxy_headers: bool = False

    storage_backend: Literal["disabled", "minio"] = "disabled"
    storage_endpoint: str = "minio:9000"
    storage_access_key: SecretStr | None = None
    storage_secret_key: SecretStr | None = None
    storage_bucket: str = "harbor-market-products"
    storage_secure: bool = False
    upload_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1, le=5 * 1024 * 1024)

    payment_mode: Literal["disabled", "mock"] = "disabled"
    payment_mock_controls_enabled: bool = False
    payment_mock_signing_secret: SecretStr | None = None
    payment_mock_app_id: str = "wx0000000000000000"
    payment_prepay_ttl_seconds: int = Field(default=2 * 60 * 60, ge=60, le=2 * 60 * 60)
    # WeChat permits ciphertext alone to reach 1 MiB; reserve bounded room for
    # the surrounding notification envelope while keeping memory use capped.
    payment_webhook_max_bytes: int = Field(
        default=1_280 * 1_024,
        ge=1_024,
        le=1_280 * 1_024,
    )

    @property
    def parsed_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def parsed_allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @model_validator(mode="after")
    def validate_security_settings(self) -> Self:
        self.storage_endpoint = self.storage_endpoint.strip()
        self.storage_bucket = self.storage_bucket.strip()
        if (
            self.auth_token_ttl_minutes is not None
            and "auth_session_ttl_seconds" not in self.model_fields_set
        ):
            self.auth_session_ttl_seconds = self.auth_token_ttl_minutes * 60
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError("SameSite=None requires a secure cookie")
        if not self.parsed_allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must contain at least one hostname")
        if self.storage_backend == "minio":
            if not self.storage_endpoint.strip():
                raise ValueError("STORAGE_ENDPOINT is required when STORAGE_BACKEND=minio")
            if "://" in self.storage_endpoint:
                raise ValueError("STORAGE_ENDPOINT must be host:port without an URL scheme")
            if self.storage_access_key is None or not self.storage_access_key.get_secret_value():
                raise ValueError("STORAGE_ACCESS_KEY is required when STORAGE_BACKEND=minio")
            if self.storage_secret_key is None or not self.storage_secret_key.get_secret_value():
                raise ValueError("STORAGE_SECRET_KEY is required when STORAGE_BACKEND=minio")
            if len(self.storage_access_key.get_secret_value()) < 3:
                raise ValueError("STORAGE_ACCESS_KEY must contain at least 3 characters")
            if len(self.storage_secret_key.get_secret_value()) < 8:
                raise ValueError("STORAGE_SECRET_KEY must contain at least 8 characters")
            if not re.fullmatch(
                r"(?=.{3,63}\Z)(?!.*\.\.)(?!\d+\.\d+\.\d+\.\d+\Z)"
                r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
                r"(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*",
                self.storage_bucket,
            ):
                raise ValueError("STORAGE_BUCKET must be a valid DNS-style S3 bucket name")
        if self.payment_mock_controls_enabled and self.payment_mode != "mock":
            raise ValueError("PAYMENT_MOCK_CONTROLS_ENABLED requires PAYMENT_MODE=mock")
        if self.payment_mode == "mock":
            if self.environment == "production":
                raise ValueError("mock payments cannot be enabled in production")
            if (
                self.payment_mock_signing_secret is None
                or len(self.payment_mock_signing_secret.get_secret_value()) < 32
            ):
                raise ValueError(
                    "PAYMENT_MOCK_SIGNING_SECRET must contain at least 32 characters "
                    "when PAYMENT_MODE=mock"
                )
            if not re.fullmatch(r"wx[A-Za-z0-9]{16}", self.payment_mock_app_id):
                raise ValueError("PAYMENT_MOCK_APP_ID must look like a WeChat Mini Program AppID")
        if self.environment == "production":
            if _looks_like_placeholder(self.auth_secret_key.get_secret_value()):
                raise ValueError("production AUTH_SECRET_KEY must not be a placeholder")
            if _looks_like_placeholder(self.database_url.get_secret_value()):
                raise ValueError("production DATABASE_URL must not contain placeholder credentials")
            if not self.auth_cookie_secure:
                raise ValueError("production requires AUTH_COOKIE_SECURE=true")
            if self.argon2_time_cost < 2 or self.argon2_memory_cost_kib < 19_456:
                raise ValueError("production Argon2 settings are below the minimum security floor")
            if "*" in self.parsed_cors_origins:
                raise ValueError("production CORS origins must be explicit")
            if "*" in self.parsed_allowed_hosts:
                raise ValueError("production allowed hosts must be explicit")
            if self.storage_backend == "minio":
                assert self.storage_access_key is not None
                assert self.storage_secret_key is not None
                if _looks_like_placeholder(self.storage_access_key.get_secret_value()):
                    raise ValueError("production STORAGE_ACCESS_KEY must not be a placeholder")
                if _looks_like_placeholder(self.storage_secret_key.get_secret_value()):
                    raise ValueError("production STORAGE_SECRET_KEY must not be a placeholder")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


def _looks_like_placeholder(value: str) -> bool:
    return "replace-with-" in value.strip().casefold()
