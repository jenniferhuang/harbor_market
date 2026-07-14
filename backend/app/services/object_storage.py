from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import BinaryIO, Protocol, runtime_checkable

from minio import Minio
from minio.commonconfig import CopySource
from minio.error import S3Error

from app.core.config import Settings


class ObjectStorageDisabledError(RuntimeError):
    """Raised when an object operation is attempted while storage is disabled."""


class ObjectStorageSizeError(RuntimeError):
    """Raised when a bounded read exceeds its allowed byte count."""


class ObjectStorageNotFoundError(FileNotFoundError):
    """Raised when an object key does not exist in the configured bucket."""


@dataclass(frozen=True, slots=True)
class ObjectStat:
    object_name: str
    size: int
    content_type: str | None = None
    etag: str | None = None
    version_id: str | None = None
    last_modified: datetime | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@runtime_checkable
class ObjectStorage(Protocol):
    def ensure_bucket(self) -> None: ...

    def put(
        self,
        object_name: str,
        data: BinaryIO | bytes,
        length: int | None = None,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectStat: ...

    def get(self, object_name: str, *, max_bytes: int | None = None) -> bytes: ...

    def copy(
        self,
        source_name: str,
        destination_name: str,
        *,
        source_etag: str | None = None,
    ) -> ObjectStat: ...

    def stat(self, object_name: str) -> ObjectStat: ...

    def delete(self, object_name: str) -> None: ...

    def object_exists(self, object_name: str) -> bool: ...


class DisabledObjectStorage:
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
        del object_name, data, length, content_type, metadata
        raise ObjectStorageDisabledError("object storage is disabled")

    def get(self, object_name: str, *, max_bytes: int | None = None) -> bytes:
        del object_name, max_bytes
        raise ObjectStorageDisabledError("object storage is disabled")

    def copy(
        self,
        source_name: str,
        destination_name: str,
        *,
        source_etag: str | None = None,
    ) -> ObjectStat:
        del source_name, destination_name, source_etag
        raise ObjectStorageDisabledError("object storage is disabled")

    def stat(self, object_name: str) -> ObjectStat:
        del object_name
        raise ObjectStorageDisabledError("object storage is disabled")

    def delete(self, object_name: str) -> None:
        del object_name
        raise ObjectStorageDisabledError("object storage is disabled")

    def object_exists(self, object_name: str) -> bool:
        del object_name
        return False


class MinioObjectStorage:
    _MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "NoSuchObject"})

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
        client: Minio | None = None,
    ) -> None:
        self.bucket = bucket
        self._client = client or Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

    def ensure_bucket(self) -> None:
        if self._client.bucket_exists(self.bucket):
            return
        try:
            self._client.make_bucket(self.bucket)
        except S3Error as error:
            if error.code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                raise
            if not self._client.bucket_exists(self.bucket):
                raise

    def put(
        self,
        object_name: str,
        data: BinaryIO | bytes,
        length: int | None = None,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectStat:
        object_name = _validated_object_name(object_name)
        if isinstance(data, bytes):
            actual_length = len(data)
            if length is not None and length != actual_length:
                raise ValueError("length does not match the byte payload")
            length = actual_length
            stream: BinaryIO = BytesIO(data)
        else:
            if length is None:
                raise ValueError("length is required for streamed uploads")
            stream = data
        if length < 0:
            raise ValueError("length must be non-negative")

        result = self._client.put_object(
            self.bucket,
            object_name,
            stream,
            length,
            content_type=content_type,
            metadata=dict(metadata or {}),
        )
        return ObjectStat(
            object_name=object_name,
            size=length,
            content_type=content_type,
            etag=getattr(result, "etag", None),
            version_id=getattr(result, "version_id", None),
            metadata=dict(metadata or {}),
        )

    def get(self, object_name: str, *, max_bytes: int | None = None) -> bytes:
        object_name = _validated_object_name(object_name)
        if max_bytes is not None and max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        try:
            response = self._client.get_object(self.bucket, object_name)
        except S3Error as error:
            if error.code in self._MISSING_OBJECT_CODES:
                raise ObjectStorageNotFoundError(object_name) from error
            raise
        try:
            payload = response.read() if max_bytes is None else response.read(max_bytes + 1)
            if max_bytes is not None and len(payload) > max_bytes:
                raise ObjectStorageSizeError(
                    f"object exceeds the bounded read limit of {max_bytes} bytes"
                )
            return payload
        finally:
            try:
                response.close()
            finally:
                response.release_conn()

    def copy(
        self,
        source_name: str,
        destination_name: str,
        *,
        source_etag: str | None = None,
    ) -> ObjectStat:
        source_name = _validated_object_name(source_name)
        destination_name = _validated_object_name(destination_name)
        try:
            result = self._client.copy_object(
                self.bucket,
                destination_name,
                CopySource(self.bucket, source_name, match_etag=source_etag),
            )
        except S3Error as error:
            if error.code in self._MISSING_OBJECT_CODES:
                raise ObjectStorageNotFoundError(source_name) from error
            raise
        copied = self.stat(destination_name)
        return ObjectStat(
            object_name=destination_name,
            size=copied.size,
            content_type=copied.content_type,
            etag=getattr(result, "etag", None) or copied.etag,
            version_id=getattr(result, "version_id", None) or copied.version_id,
            last_modified=copied.last_modified,
            metadata=copied.metadata,
        )

    def stat(self, object_name: str) -> ObjectStat:
        object_name = _validated_object_name(object_name)
        try:
            result = self._client.stat_object(self.bucket, object_name)
        except S3Error as error:
            if error.code in self._MISSING_OBJECT_CODES:
                raise ObjectStorageNotFoundError(object_name) from error
            raise
        return ObjectStat(
            object_name=object_name,
            size=result.size,
            content_type=getattr(result, "content_type", None),
            etag=getattr(result, "etag", None),
            version_id=getattr(result, "version_id", None),
            last_modified=getattr(result, "last_modified", None),
            metadata=dict(getattr(result, "metadata", {}) or {}),
        )

    def delete(self, object_name: str) -> None:
        self._client.remove_object(self.bucket, _validated_object_name(object_name))

    def object_exists(self, object_name: str) -> bool:
        try:
            self.stat(object_name)
        except ObjectStorageNotFoundError:
            return False
        return True


def build_object_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend == "disabled":
        return DisabledObjectStorage()

    access_key = settings.storage_access_key
    secret_key = settings.storage_secret_key
    if access_key is None or secret_key is None:
        raise ValueError("MinIO credentials are missing")
    storage = MinioObjectStorage(
        endpoint=settings.storage_endpoint,
        access_key=access_key.get_secret_value(),
        secret_key=secret_key.get_secret_value(),
        bucket=settings.storage_bucket,
        secure=settings.storage_secure,
    )
    storage.ensure_bucket()
    return storage


def _validated_object_name(object_name: str) -> str:
    if not object_name or object_name.startswith("/") or object_name.endswith("/"):
        raise ValueError("object_name must be a relative object path")
    if "\\" in object_name or any(
        ord(character) < 32 or ord(character) == 127 for character in object_name
    ):
        raise ValueError("object_name contains unsupported characters")
    parts = object_name.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("object_name contains an unsafe path segment")
    if len(object_name) > 512 or len(object_name.encode("utf-8")) > 1024:
        raise ValueError("object_name exceeds the application storage-key limit")
    return object_name
