from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from io import BytesIO
from typing import BinaryIO

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("AUTH_SECRET_KEY", "test-only-signing-key-that-is-at-least-32-characters")
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.models import User
from app.services.object_storage import ObjectStat


class FakeObjectStorage:
    """Small in-memory ObjectStorage implementation for API integration tests."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.stats: dict[str, ObjectStat] = {}
        self.put_calls: list[str] = []
        self.copy_calls: list[tuple[str, str]] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.ensure_bucket_calls = 0
        self.fail_put = False
        self.fail_copy = False
        self.copy_then_fail = False
        self.fail_get = False
        self.fail_stat = False
        self.fail_delete = False

    def ensure_bucket(self) -> None:
        self.ensure_bucket_calls += 1

    def put(
        self,
        object_name: str,
        data: BinaryIO | bytes,
        length: int | None = None,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectStat:
        if self.fail_put:
            raise RuntimeError("fake object storage put failure")
        payload = data if isinstance(data, bytes) else data.read()
        if length is not None and length != len(payload):
            raise ValueError("length does not match payload")
        self.put_calls.append(object_name)
        self.objects[object_name] = payload
        result = ObjectStat(
            object_name=object_name,
            size=len(payload),
            content_type=content_type,
            etag=f"fake-{len(self.put_calls)}",
            last_modified=datetime.now(UTC),
            metadata=dict(metadata or {}),
        )
        self.stats[object_name] = result
        return result

    def get(self, object_name: str, *, max_bytes: int | None = None) -> bytes:
        self.get_calls.append(object_name)
        if self.fail_get:
            raise RuntimeError("fake object storage get failure")
        if object_name not in self.objects:
            from app.services.object_storage import ObjectStorageNotFoundError

            raise ObjectStorageNotFoundError(object_name)
        payload = self.objects[object_name]
        if max_bytes is not None and len(payload) > max_bytes:
            from app.services.object_storage import ObjectStorageSizeError

            raise ObjectStorageSizeError("fake object exceeds bounded read")
        return payload

    def copy(
        self,
        source_name: str,
        destination_name: str,
        *,
        source_etag: str | None = None,
    ) -> ObjectStat:
        self.copy_calls.append((source_name, destination_name))
        if self.fail_copy:
            raise RuntimeError("fake object storage copy failure")
        source = self.stats[source_name]
        if source_etag is not None and source.etag != source_etag:
            raise RuntimeError("fake object etag precondition failed")
        copied = self.put(
            destination_name,
            self.objects[source_name],
            content_type=source.content_type or "application/octet-stream",
            metadata=source.metadata,
        )
        if self.copy_then_fail:
            raise RuntimeError("fake object storage copy failed after destination creation")
        return copied

    def stat(self, object_name: str) -> ObjectStat:
        if self.fail_stat:
            raise RuntimeError("fake object storage stat failure")
        if object_name not in self.stats:
            from app.services.object_storage import ObjectStorageNotFoundError

            raise ObjectStorageNotFoundError(object_name)
        return self.stats[object_name]

    def delete(self, object_name: str) -> None:
        self.delete_calls.append(object_name)
        if self.fail_delete:
            raise RuntimeError("fake object storage delete failure")
        if object_name not in self.objects:
            from app.services.object_storage import ObjectStorageNotFoundError

            raise ObjectStorageNotFoundError(object_name)
        del self.objects[object_name]
        del self.stats[object_name]

    def object_exists(self, object_name: str) -> bool:
        return object_name in self.objects

    def seed(
        self,
        object_name: str,
        payload: bytes = b"fake-image",
        *,
        content_type: str = "image/png",
        reported_size: int | None = None,
    ) -> ObjectStat:
        result = self.put(
            object_name,
            BytesIO(payload),
            len(payload),
            content_type=content_type,
        )
        if reported_size is None:
            return result
        overridden = ObjectStat(
            object_name=result.object_name,
            size=reported_size,
            content_type=result.content_type,
            etag=result.etag,
            last_modified=result.last_modified,
            metadata=result.metadata,
        )
        self.stats[object_name] = overridden
        return overridden


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
def fake_object_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def app(
    settings: Settings,
    fake_object_storage: FakeObjectStorage,
) -> Iterator[FastAPI]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    def enable_sqlite_foreign_keys(
        dbapi_connection: DBAPIConnection,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()

    event.listen(engine, "connect", enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    application = create_app(settings, engine=engine, object_storage=fake_object_storage)
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


@pytest.fixture
def admin_client(client: TestClient, app: FastAPI) -> TestClient:
    credentials = {"username": "catalog-admin", "password": "correct horse battery staple"}
    response = client.post("/api/v1/auth/register", json=credentials)
    assert response.status_code == 201
    user_id = response.json()["data"]["id"]
    with app.state.session_factory() as session:
        user = session.get(User, user_id)
        assert user is not None
        user.is_admin = True
        session.commit()
    login = client.post("/api/v1/auth/login", json=credentials)
    assert login.status_code == 200
    assert login.json()["data"]["is_admin"] is True
    return client
