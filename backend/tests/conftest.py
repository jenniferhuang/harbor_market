from __future__ import annotations

import os
from collections.abc import Iterator

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("AUTH_SECRET_KEY", "test-only-signing-key-that-is-at-least-32-characters")
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+pysqlite://",
        auth_secret_key="test-only-signing-key-that-is-at-least-32-characters",
        auth_cookie_secure=False,
        allowed_hosts=("testserver,localhost,127.0.0.1,app.hermes-node.com,*.trycloudflare.com"),
        argon2_time_cost=1,
        argon2_memory_cost_kib=8_192,
        argon2_parallelism=1,
        registration_rate_limit=10,
        login_failure_rate_limit=2,
        rate_limit_max_keys=100,
    )


@pytest.fixture
def app(settings: Settings) -> Iterator[FastAPI]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    application = create_app(settings, engine=engine)
    yield application
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def registered_user(client: TestClient) -> dict[str, str]:
    credentials = {"username": "alice", "password": "correct horse battery staple"}
    response = client.post("/api/v1/auth/register", json=credentials)
    assert response.status_code == 201
    return credentials
