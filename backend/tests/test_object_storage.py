from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from minio.error import S3Error

from app.core.config import Settings
from app.main import create_app
from app.services.object_storage import (
    DisabledObjectStorage,
    MinioObjectStorage,
    ObjectStorage,
    ObjectStorageDisabledError,
    ObjectStorageNotFoundError,
    ObjectStorageSizeError,
    build_object_storage,
)


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.closed = False
        self.released = False

    def read(self, amount: int | None = None) -> bytes:
        return self.payload if amount is None else self.payload[:amount]

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeMinioClient:
    def __init__(self) -> None:
        self.bucket_present = False
        self.created_buckets: list[str] = []
        self.put_calls: list[dict[str, Any]] = []
        self.removed: list[tuple[str, str]] = []
        self.copy_calls: list[tuple[str, str, Any]] = []
        self.response = FakeResponse(b"image-bytes")
        self.missing = False

    def bucket_exists(self, bucket: str) -> bool:
        del bucket
        return self.bucket_present

    def make_bucket(self, bucket: str) -> None:
        self.created_buckets.append(bucket)
        self.bucket_present = True

    def put_object(
        self,
        bucket: str,
        object_name: str,
        data: BytesIO,
        length: int,
        **kwargs: Any,
    ) -> SimpleNamespace:
        self.put_calls.append(
            {
                "bucket": bucket,
                "object_name": object_name,
                "payload": data.read(),
                "length": length,
                **kwargs,
            }
        )
        return SimpleNamespace(etag="etag-1", version_id="version-1")

    def get_object(self, bucket: str, object_name: str) -> FakeResponse:
        if self.missing:
            raise _missing_error(bucket, object_name)
        return self.response

    def copy_object(self, bucket: str, object_name: str, source: Any) -> SimpleNamespace:
        self.copy_calls.append((bucket, object_name, source))
        return SimpleNamespace(etag="copied-etag", version_id="copied-version")

    def stat_object(self, bucket: str, object_name: str) -> SimpleNamespace:
        if self.missing:
            raise _missing_error(bucket, object_name)
        return SimpleNamespace(
            size=11,
            content_type="image/webp",
            etag="etag-1",
            version_id="version-1",
            last_modified=datetime(2026, 1, 1, tzinfo=UTC),
            metadata={"x-amz-meta-product-id": "123"},
        )

    def remove_object(self, bucket: str, object_name: str) -> None:
        self.removed.append((bucket, object_name))


def _missing_error(bucket: str, object_name: str) -> S3Error:
    return S3Error(
        response=SimpleNamespace(),
        code="NoSuchKey",
        message="missing",
        resource=object_name,
        request_id="request-1",
        host_id="host-1",
        bucket_name=bucket,
        object_name=object_name,
    )


@pytest.fixture
def fake_client() -> FakeMinioClient:
    return FakeMinioClient()


@pytest.fixture
def storage(fake_client: FakeMinioClient) -> MinioObjectStorage:
    return MinioObjectStorage(
        endpoint="minio:9000",
        access_key="access-key",
        secret_key="secret-key",
        bucket="harbor-market-products",
        client=fake_client,  # type: ignore[arg-type]
    )


def test_minio_storage_ensures_private_bucket_without_setting_a_policy(
    storage: MinioObjectStorage,
    fake_client: FakeMinioClient,
) -> None:
    storage.ensure_bucket()
    storage.ensure_bucket()

    assert fake_client.created_buckets == ["harbor-market-products"]


def test_minio_storage_put_get_stat_delete_and_exists(
    storage: MinioObjectStorage,
    fake_client: FakeMinioClient,
) -> None:
    stored = storage.put(
        "products/123/hero.webp",
        b"image-bytes",
        content_type="image/webp",
        metadata={"product-id": "123"},
    )

    assert stored.object_name == "products/123/hero.webp"
    assert stored.size == 11
    assert stored.etag == "etag-1"
    assert fake_client.put_calls == [
        {
            "bucket": "harbor-market-products",
            "object_name": "products/123/hero.webp",
            "payload": b"image-bytes",
            "length": 11,
            "content_type": "image/webp",
            "metadata": {"product-id": "123"},
        }
    ]

    assert storage.get("products/123/hero.webp") == b"image-bytes"
    assert fake_client.response.closed is True
    assert fake_client.response.released is True

    stat = storage.stat("products/123/hero.webp")
    assert stat.size == 11
    assert stat.content_type == "image/webp"
    assert stat.metadata == {"x-amz-meta-product-id": "123"}
    assert storage.object_exists("products/123/hero.webp") is True

    storage.delete("products/123/hero.webp")
    assert fake_client.removed == [("harbor-market-products", "products/123/hero.webp")]

    fake_client.missing = True
    assert storage.object_exists("products/123/missing.webp") is False


def test_minio_storage_enforces_bounded_reads_and_releases_response(
    storage: MinioObjectStorage,
    fake_client: FakeMinioClient,
) -> None:
    fake_client.response = FakeResponse(b"four")

    assert storage.get("products/123/exact.webp", max_bytes=4) == b"four"
    assert fake_client.response.closed is True
    assert fake_client.response.released is True

    fake_client.response = FakeResponse(b"five!")
    with pytest.raises(ObjectStorageSizeError):
        storage.get("products/123/oversized.webp", max_bytes=4)
    assert fake_client.response.closed is True
    assert fake_client.response.released is True


def test_minio_storage_conditional_copy_and_missing_get_mapping(
    storage: MinioObjectStorage,
    fake_client: FakeMinioClient,
) -> None:
    copied = storage.copy(
        "products/staged/CODE/source.webp",
        "products/catalog/CODE/gallery/destination.webp",
        source_etag="source-etag",
    )

    bucket, destination, source = fake_client.copy_calls[0]
    assert bucket == "harbor-market-products"
    assert destination == "products/catalog/CODE/gallery/destination.webp"
    assert source.bucket_name == "harbor-market-products"
    assert source.object_name == "products/staged/CODE/source.webp"
    assert source.match_etag == "source-etag"
    assert copied.etag == "copied-etag"
    assert copied.version_id == "copied-version"

    fake_client.missing = True
    with pytest.raises(ObjectStorageNotFoundError):
        storage.get("products/staged/CODE/missing.webp", max_bytes=10)


@pytest.mark.parametrize(
    "object_name",
    [
        "",
        "/absolute.webp",
        "trailing/",
        "products//hero.webp",
        "../hero.webp",
        "a\\b",
        "products/hero\x7f.webp",
    ],
)
def test_minio_storage_rejects_unsafe_object_names(
    storage: MinioObjectStorage,
    object_name: str,
) -> None:
    with pytest.raises(ValueError):
        storage.put(object_name, b"content")


def test_minio_storage_requires_stream_length(storage: MinioObjectStorage) -> None:
    with pytest.raises(ValueError, match="length is required"):
        storage.put("products/123/hero.webp", BytesIO(b"image-bytes"))


def test_disabled_storage_is_a_safe_default(settings: Settings) -> None:
    storage = build_object_storage(settings)

    assert isinstance(storage, DisabledObjectStorage)
    assert isinstance(storage, ObjectStorage)
    assert storage.object_exists("products/123/hero.webp") is False
    with pytest.raises(ObjectStorageDisabledError):
        storage.put("products/123/hero.webp", b"image-bytes")


def test_minio_settings_require_credentials(settings: Settings) -> None:
    values = settings.model_dump()
    values["storage_backend"] = "minio"
    values["storage_access_key"] = None
    values["storage_secret_key"] = None

    with pytest.raises(ValueError, match="STORAGE_ACCESS_KEY"):
        Settings(_env_file=None, **values)


def test_create_app_accepts_injected_object_storage(app: FastAPI, settings: Settings) -> None:
    injected = DisabledObjectStorage()

    application = create_app(settings, engine=app.state.engine, object_storage=injected)

    assert application.state.object_storage is injected
