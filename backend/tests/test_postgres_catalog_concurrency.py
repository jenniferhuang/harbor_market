from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from typing import BinaryIO

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_admin
from app.api.routes import admin_catalog
from app.core.config import Settings
from app.main import create_app
from app.models import Category, ObjectCleanupJob, Product, ProductImage, ProductSku, User
from app.services.object_storage import ObjectStat, ObjectStorageNotFoundError

pytestmark = pytest.mark.postgres

_ACTIVE_CLEANUP_STATUSES = ("intent", "pending", "processing", "failed")


class _ConcurrentObjectStorage:
    def __init__(self, *, direct_put_barrier: Barrier | None = None) -> None:
        self._direct_put_barrier = direct_put_barrier
        self._lock = Lock()
        self.objects: dict[str, bytes] = {}
        self.stats: dict[str, ObjectStat] = {}
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []

    def ensure_bucket(self) -> None:
        return None

    def put(
        self,
        object_name: str,
        data: BinaryIO | bytes,
        length: int | None = None,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectStat:
        payload = data if isinstance(data, bytes) else data.read()
        if length is not None and length != len(payload):
            raise ValueError("length does not match payload")
        if self._direct_put_barrier is not None and "/gallery/" in object_name:
            self._direct_put_barrier.wait(timeout=10)
        result = ObjectStat(
            object_name=object_name,
            size=len(payload),
            content_type=content_type,
            etag=hashlib.sha256(payload + object_name.encode()).hexdigest(),
            last_modified=datetime.now(UTC),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self.put_calls.append(object_name)
            self.objects[object_name] = payload
            self.stats[object_name] = result
        return result

    def get(self, object_name: str, *, max_bytes: int | None = None) -> bytes:
        with self._lock:
            try:
                payload = self.objects[object_name]
            except KeyError as exc:
                raise ObjectStorageNotFoundError(object_name) from exc
        if max_bytes is not None and len(payload) > max_bytes:
            raise ValueError("object exceeds bounded read")
        return payload

    def copy(
        self,
        source_name: str,
        destination_name: str,
        *,
        source_etag: str | None = None,
    ) -> ObjectStat:
        source = self.stat(source_name)
        if source_etag is not None and source.etag != source_etag:
            raise ValueError("source etag changed")
        return self.put(
            destination_name,
            self.get(source_name),
            content_type=source.content_type or "application/octet-stream",
            metadata=source.metadata,
        )

    def stat(self, object_name: str) -> ObjectStat:
        with self._lock:
            try:
                return self.stats[object_name]
            except KeyError as exc:
                raise ObjectStorageNotFoundError(object_name) from exc

    def delete(self, object_name: str) -> None:
        with self._lock:
            self.delete_calls.append(object_name)
            if object_name not in self.objects:
                raise ObjectStorageNotFoundError(object_name)
            del self.objects[object_name]
            del self.stats[object_name]

    def object_exists(self, object_name: str) -> bool:
        with self._lock:
            return object_name in self.objects


@pytest.fixture(scope="module")
def postgres_database() -> Iterator[tuple[Engine, str]]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = make_url(database_url).database or ""
    if "test" not in database_name.casefold():
        pytest.skip("TEST_DATABASE_URL database name must contain 'test'")

    backend_dir = Path(__file__).resolve().parents[1]
    environment = {
        "DATABASE_URL": database_url,
        "PATH": "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        check=True,
    )
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    try:
        yield engine, database_url
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_postgres_catalog(postgres_database: tuple[Engine, str]) -> Iterator[None]:
    engine, _database_url = postgres_database
    _truncate_application_tables(engine)
    try:
        yield
    finally:
        _truncate_application_tables(engine)


def test_concurrent_gallery_uploads_stop_at_eight(
    postgres_database: tuple[Engine, str],
) -> None:
    engine, database_url = postgres_database
    admin_id, product_id = _seed_admin_and_product(engine, gallery_count=7)
    storage = _ConcurrentObjectStorage(direct_put_barrier=Barrier(2))
    app = _test_app(engine, database_url, storage, admin_id)

    responses = _concurrent_image_posts(
        app,
        f"/api/v1/admin/products/{product_id}/images",
        {"image_type": "gallery", "alt_text": "并发图片", "sort_order": "8"},
    )

    assert sorted(response.status_code for response in responses) == [200, 422]
    rejected = next(response for response in responses if response.status_code == 422)
    assert rejected.json()["error"]["code"] == "image_limit_exceeded"
    with Session(engine) as session:
        gallery_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(ProductImage)
            .where(
                ProductImage.product_id == product_id,
                ProductImage.image_type == "gallery",
            )
        )
    assert gallery_count == 8
    assert len(storage.put_calls) == 2
    assert len(storage.delete_calls) == 1


def test_concurrent_staging_uploads_stop_at_per_product_quota(
    postgres_database: tuple[Engine, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, database_url = postgres_database
    admin_id, _product_id = _seed_admin_and_product(engine)
    _seed_staging_jobs(engine, admin_id, product_code="QUOTA", count=99)
    storage = _ConcurrentObjectStorage()
    app = _test_app(engine, database_url, storage, admin_id)

    original_lock = admin_catalog._lock_staging_quota
    arrival_barrier = Barrier(2)

    def synchronized_quota_lock(session: Session) -> None:
        arrival_barrier.wait(timeout=10)
        original_lock(session)

    monkeypatch.setattr(admin_catalog, "_lock_staging_quota", synchronized_quota_lock)
    responses = _concurrent_image_posts(
        app,
        "/api/v1/admin/product-images/staging",
        {"product_code": "QUOTA"},
    )

    assert sorted(response.status_code for response in responses) == [200, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "staging_quota_exceeded"
    with Session(engine) as session:
        staged_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(ObjectCleanupJob)
            .where(
                ObjectCleanupJob.reason == "staging_expiry",
                ObjectCleanupJob.status.in_(_ACTIVE_CLEANUP_STATUSES),
                ObjectCleanupJob.object_key.startswith("products/staged/QUOTA/"),
            )
        )
    assert staged_count == 100
    assert len(storage.put_calls) == 1


def _truncate_application_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "TRUNCATE TABLE payment_provider_events, payment_state_events, "
                "payment_mock_provider_records, payment_attempts, object_cleanup_jobs, "
                "import_jobs, product_images, "
                "product_skus, products, categories, users RESTART IDENTITY CASCADE"
            )
        )


def _seed_admin_and_product(engine: Engine, *, gallery_count: int = 0) -> tuple[int, int]:
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            username="postgres-review-admin",
            password_hash="$argon2id$review",
            is_admin=True,
        )
        category = Category(code="REVIEW", name="并发验收")
        product = Product(
            product_code="REVIEW-PRODUCT",
            name="并发验收商品",
            category=category,
            base_price_cents=1_900,
        )
        product.skus.append(
            ProductSku(
                sku_code="REVIEW-SKU",
                name="默认规格",
                price_cents=1_900,
                is_default=True,
            )
        )
        product.images.extend(
            ProductImage(
                object_key=f"products/review/gallery/{index}.png",
                image_type="gallery",
                alt_text=f"图片 {index}",
                sort_order=index,
                mime_type="image/png",
                size_bytes=100,
                width=3,
                height=2,
            )
            for index in range(gallery_count)
        )
        session.add_all([admin, product])
        session.commit()
        return admin.id, product.id


def _seed_staging_jobs(
    engine: Engine,
    admin_id: int,
    *,
    product_code: str,
    count: int,
) -> None:
    with Session(engine) as session:
        session.add_all(
            ObjectCleanupJob(
                created_by=admin_id,
                object_key=f"products/staged/{product_code}/{index:032x}.png",
                reason="staging_expiry",
                status="pending",
                not_before=datetime.now(UTC) + timedelta(days=7),
            )
            for index in range(count)
        )
        session.commit()


def _test_app(
    engine: Engine,
    database_url: str,
    storage: _ConcurrentObjectStorage,
    admin_id: int,
) -> FastAPI:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        auth_secret_key="postgres-concurrency-test-signing-key",
        auth_cookie_secure=False,
        allowed_hosts="testserver",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8_192,
        argon2_parallelism=1,
    )
    app = create_app(settings, engine=engine, object_storage=storage)
    admin = SimpleNamespace(id=admin_id, is_admin=True)

    def override_admin() -> SimpleNamespace:
        return admin

    app.dependency_overrides[get_current_admin] = override_admin
    return app


def _concurrent_image_posts(
    app: FastAPI,
    path: str,
    data: dict[str, str],
) -> list[Response]:
    payload = _image_bytes()

    def post(client: TestClient) -> Response:
        return client.post(
            path,
            files={"file": ("product.png", payload, "image/png")},
            data=data,
        )

    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app)) for _ in range(2)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(post, client) for client in clients]
            return [future.result(timeout=20) for future in futures]


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (3, 2), color=(31, 92, 77)).save(output, format="PNG")
    return output.getvalue()
