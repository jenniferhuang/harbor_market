from __future__ import annotations

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
    auth_cookie_path: str = "/api/v1/auth"
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

    @property
    def parsed_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    @property
    def parsed_allowed_hosts(self) -> list[str]:
        return [host.strip() for host in self.allowed_hosts.split(",") if host.strip()]

    @model_validator(mode="after")
    def validate_security_settings(self) -> Self:
        if (
            self.auth_token_ttl_minutes is not None
            and "auth_session_ttl_seconds" not in self.model_fields_set
        ):
            self.auth_session_ttl_seconds = self.auth_token_ttl_minutes * 60
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError("SameSite=None requires a secure cookie")
        if not self.parsed_allowed_hosts:
            raise ValueError("ALLOWED_HOSTS must contain at least one hostname")
        if self.environment == "production":
            if not self.auth_cookie_secure:
                raise ValueError("production requires AUTH_COOKIE_SECURE=true")
            if self.argon2_time_cost < 2 or self.argon2_memory_cost_kib < 19_456:
                raise ValueError("production Argon2 settings are below the minimum security floor")
            if "*" in self.parsed_cors_origins:
                raise ValueError("production CORS origins must be explicit")
            if "*" in self.parsed_allowed_hosts:
                raise ValueError("production allowed hosts must be explicit")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
