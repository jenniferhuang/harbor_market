from __future__ import annotations

import hashlib
import warnings
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import MiniCustomer, ObjectCleanupJob
from app.services.object_cleanup import enqueue_object_cleanup, run_object_cleanup_jobs
from app.services.object_storage import ObjectStorage

_MAX_PIXELS = 16_000_000
_MAX_ENCODED_BYTES = 1024 * 1024


def prepare_avatar(payload: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                if (
                    image.format not in {"JPEG", "PNG", "WEBP"}
                    or getattr(image, "n_frames", 1) != 1
                    or image.width * image.height > _MAX_PIXELS
                ):
                    raise ValueError("Unsupported avatar")
                image.verify()
            with Image.open(BytesIO(payload)) as image:
                image.load()
                oriented = ImageOps.exif_transpose(image)
                oriented.thumbnail((512, 512), Image.Resampling.LANCZOS)
                # A fresh RGB image discards EXIF, GPS, comments and embedded profiles.
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                output = BytesIO()
                clean.save(output, format="JPEG", quality=85, optimize=True)
                result = output.getvalue()
                if len(result) > _MAX_ENCODED_BYTES:
                    raise ValueError("Encoded avatar exceeds limit")
                return result
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        EOFError,
        OverflowError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ApiError(
            400,
            "mini_avatar_invalid",
            "头像无效，请选择静态 JPG、PNG 或 WebP 图片，像素不能超过1600万",
        ) from exc


def replace_avatar(
    session: Session,
    storage: ObjectStorage,
    customer: MiniCustomer,
    payload: bytes,
) -> MiniCustomer:
    customer_id = customer.id
    object_key = f"customers/{customer_id}/avatars/{uuid4().hex}.jpg"
    digest = hashlib.sha256(payload).hexdigest()
    intent = enqueue_object_cleanup(
        session,
        [object_key],
        reason="mini_avatar_upload_intent",
        status="intent",
    )[0]
    intent_id = intent.id
    session.commit()
    try:
        storage.put(
            object_key,
            payload,
            content_type="image/jpeg",
            metadata={"sha256": digest},
        )
        locked_customer = session.scalar(
            select(MiniCustomer)
            .where(MiniCustomer.id == customer_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if locked_customer is None or not locked_customer.is_active:
            raise ApiError(403, "mini_customer_inactive", "用户已停用")
        old_key = locked_customer.avatar_object_key
        locked_customer.avatar_object_key = object_key
        locked_customer.avatar_sha256 = digest
        locked_customer.avatar_size_bytes = len(payload)
        locked_customer.updated_at = datetime.now(UTC)
        intent = session.get(ObjectCleanupJob, intent_id)
        assert intent is not None
        intent.status = "completed"
        intent.completed_at = datetime.now(UTC)
        old_jobs = (
            enqueue_object_cleanup(session, [old_key], reason="mini_avatar_replaced")
            if old_key is not None
            else []
        )
        session.commit()
    except Exception as exc:
        _cleanup_failed_upload(session, storage, intent_id)
        if isinstance(exc, ApiError):
            raise
        raise ApiError(503, "mini_avatar_unavailable", "头像暂时无法保存，请稍后重试") from exc

    # Replacement remains successful if deleting the previous object needs a retry.
    # The existing cleanup worker owns durable retries and protects referenced keys.
    run_object_cleanup_jobs(session, storage, old_jobs)
    session.refresh(locked_customer)
    return locked_customer


def read_avatar(storage: ObjectStorage, customer: MiniCustomer) -> bytes:
    if customer.avatar_object_key is None:
        raise ApiError(404, "mini_avatar_not_found", "尚未设置头像")
    try:
        payload = storage.get(customer.avatar_object_key, max_bytes=_MAX_ENCODED_BYTES)
        if (
            len(payload) != customer.avatar_size_bytes
            or hashlib.sha256(payload).hexdigest() != customer.avatar_sha256
        ):
            raise ValueError("Stored avatar integrity mismatch")
    except Exception as exc:
        raise ApiError(503, "mini_avatar_unavailable", "头像暂时无法读取，请稍后重试") from exc
    return payload


def _cleanup_failed_upload(session: Session, storage: ObjectStorage, intent_id: int) -> None:
    session.rollback()
    intent = session.get(ObjectCleanupJob, intent_id)
    if intent is not None and intent.status != "completed":
        intent.status = "pending"
        intent.not_before = None
        session.commit()
        run_object_cleanup_jobs(session, storage, [intent])
