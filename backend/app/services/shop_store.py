from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import Category, MiniCustomer, ObjectCleanupJob, ShopMedia, ShopProfile
from app.schemas.shop_store import AdminStorePatch, ShopMediaCreate, ShopMediaPatch, StorePatch
from app.services.object_cleanup import enqueue_object_cleanup, run_object_cleanup_jobs
from app.services.object_storage import ObjectStorage

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_VIDEO_BYTES = 10 * 1024 * 1024
_MAX_PIXELS = 16_000_000
_MAX_ACTIVE_CAROUSEL = 20
_DEFAULT_STORE = {
    "name": "港湾集市",
    "phone": "",
    "address": "",
    "latitude": None,
    "longitude": None,
}


def media_url(media_id: int) -> str:
    return f"/api/v1/shop/media/{media_id}"


def get_store(session: Session) -> ShopProfile:
    """Initialize the singleton only for mutations; a savepoint handles racing inserts."""
    store = session.get(ShopProfile, 1)
    if store is None:
        try:
            with session.begin_nested():
                store = ShopProfile(id=1, **_DEFAULT_STORE)
                session.add(store)
                session.flush()
        except IntegrityError:
            store = session.get(ShopProfile, 1, populate_existing=True)
            if store is None:
                raise
    return store


def _locked_store(session: Session) -> ShopProfile:
    get_store(session)
    store = session.scalar(
        select(ShopProfile)
        .where(ShopProfile.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert store is not None
    return store


def public_store(session: Session) -> dict[str, object]:
    store = session.get(ShopProfile, 1)
    data = (
        dict(_DEFAULT_STORE)
        if store is None
        else {key: getattr(store, key) for key in _DEFAULT_STORE}
    )
    announcement_id = session.scalar(
        select(ShopMedia.id)
        .where(ShopMedia.kind == "announcement", ShopMedia.is_active.is_(True))
        .order_by(ShopMedia.id)
        .limit(1)
    )
    data["announcement_image_url"] = media_url(announcement_id) if announcement_id else None
    return data


def admin_store(session: Session) -> dict[str, object]:
    store = session.get(ShopProfile, 1)
    return {
        **public_store(session),
        "owner_customer_id": store.owner_customer_id if store else None,
    }


def can_manage_store(session: Session, customer_id: int) -> bool:
    store = session.get(ShopProfile, 1)
    return store is not None and store.owner_customer_id == customer_id


def require_store_owner(session: Session, customer_id: int) -> None:
    if not can_manage_store(session, customer_id):
        raise ApiError(403, "shop_owner_required", "仅门店负责人可以修改门店信息")


def _check_owner(session: Session, store: ShopProfile, customer_id: int | None) -> None:
    if customer_id is None:
        return
    customer = session.get(MiniCustomer, customer_id, populate_existing=True)
    if store.owner_customer_id != customer_id or customer is None or not customer.is_active:
        raise ApiError(403, "shop_owner_required", "仅门店负责人可以修改门店信息")


def update_store(
    session: Session,
    payload: StorePatch | AdminStorePatch,
    *,
    owner_customer_id: int | None = None,
) -> ShopProfile:
    store = _locked_store(session)
    _check_owner(session, store, owner_customer_id)
    changes = payload.model_dump(exclude_unset=True)
    assigned_owner = changes.get("owner_customer_id")
    if assigned_owner is not None:
        customer = session.scalar(
            select(MiniCustomer)
            .where(MiniCustomer.id == assigned_owner, MiniCustomer.is_active.is_(True))
            .with_for_update()
        )
        if customer is None:
            raise ApiError(400, "shop_owner_invalid", "请选择已登录且未停用的小程序用户作为负责人")
    for field, value in changes.items():
        setattr(store, field, value)
    session.commit()
    session.refresh(store)
    return store


def prepare_shop_image(payload: bytes) -> bytes:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                if (
                    image.format not in {"JPEG", "PNG", "WEBP"}
                    or getattr(image, "n_frames", 1) != 1
                    or image.width * image.height > _MAX_PIXELS
                ):
                    raise ValueError("Unsupported shop image")
                image.verify()
            with Image.open(BytesIO(payload)) as image:
                image.load()
                oriented = ImageOps.exif_transpose(image)
                oriented.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                rgba = oriented.convert("RGBA")
                clean = Image.new("RGB", rgba.size, "white")
                clean.paste(rgba, mask=rgba.getchannel("A"))
                output = BytesIO()
                clean.save(output, format="JPEG", quality=85, optimize=True)
                encoded = output.getvalue()
                if len(encoded) > MAX_IMAGE_BYTES:
                    raise ValueError("Encoded image exceeds limit")
                return encoded
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
            400, "shop_image_invalid", "请选择静态 JPG、PNG 或 WebP 图片，像素不能超过1600万"
        ) from exc


@dataclass(frozen=True)
class _Box:
    kind: bytes
    body: int
    end: int


def validate_shop_mp4(payload: bytes) -> None:
    """Bounded structural inspection, without decoding or executing an external program."""
    budget = [2048]

    def boxes(start: int, end: int) -> list[_Box]:
        found = []
        while start < end:
            budget[0] -= 1
            if budget[0] < 0 or end - start < 8:
                raise ValueError("Malformed MP4 boxes")
            size = int.from_bytes(payload[start : start + 4], "big")
            kind = payload[start + 4 : start + 8]
            header = 8
            if size == 1:
                if end - start < 16:
                    raise ValueError("Truncated extended box")
                size = int.from_bytes(payload[start + 8 : start + 16], "big")
                header = 16
            elif size == 0:
                size = end - start
            if size < header or start + size > end:
                raise ValueError("Invalid box size")
            found.append(_Box(kind, start + header, start + size))
            start += size
        return found

    def one(items: list[_Box], kind: bytes) -> _Box:
        matching = [box for box in items if box.kind == kind]
        if len(matching) != 1:
            raise ValueError("Required MP4 box missing or duplicated")
        return matching[0]

    def timed_header(box: _Box, minimum_v0: int, minimum_v1: int) -> tuple[int, int]:
        version = payload[box.body] if box.end > box.body else -1
        if version == 0 and box.end - box.body >= minimum_v0:
            timescale = int.from_bytes(payload[box.body + 12 : box.body + 16], "big")
            duration = int.from_bytes(payload[box.body + 16 : box.body + 20], "big")
        elif version == 1 and box.end - box.body >= minimum_v1:
            timescale = int.from_bytes(payload[box.body + 20 : box.body + 24], "big")
            duration = int.from_bytes(payload[box.body + 24 : box.body + 32], "big")
        else:
            raise ValueError("Invalid MP4 duration header")
        if timescale <= 0 or duration <= 0 or duration > timescale * 60:
            raise ValueError("MP4 duration outside limits")
        return timescale, duration

    try:
        if not payload or len(payload) > MAX_VIDEO_BYTES:
            raise ValueError("MP4 exceeds safe byte limit")
        top = boxes(0, len(payload))
        ftyp = one(top, b"ftyp")
        if top[0] != ftyp or ftyp.end - ftyp.body < 8:
            raise ValueError("Invalid MP4 file type")
        brands = payload[ftyp.body : ftyp.body + 4] + payload[ftyp.body + 8 : ftyp.end]
        if len(brands) % 4 or not any(
            brands[index : index + 4] in {b"isom", b"iso2", b"mp41", b"mp42", b"avc1", b"M4V "}
            for index in range(0, len(brands), 4)
        ):
            raise ValueError("Unsupported MP4 brands")
        if not any(box.kind == b"mdat" and box.end > box.body for box in top):
            raise ValueError("Missing MP4 media data")
        moov = one(top, b"moov")
        movie = boxes(moov.body, moov.end)
        mvhd = one(movie, b"mvhd")
        timescale, duration = timed_header(mvhd, 100, 112)
        tracks = [box for box in movie if box.kind == b"trak"]
        if not 1 <= len(tracks) <= 8:
            raise ValueError("Invalid track count")
        has_video = False
        for track in tracks:
            track_boxes = boxes(track.body, track.end)
            track_headers = [box for box in track_boxes if box.kind == b"tkhd"]
            if track_headers:
                tkhd = one(track_boxes, b"tkhd")
                version = payload[tkhd.body] if tkhd.end > tkhd.body else -1
                if version == 0 and tkhd.end - tkhd.body >= 84:
                    track_duration = int.from_bytes(payload[tkhd.body + 20 : tkhd.body + 24], "big")
                elif version == 1 and tkhd.end - tkhd.body >= 96:
                    track_duration = int.from_bytes(payload[tkhd.body + 28 : tkhd.body + 36], "big")
                else:
                    raise ValueError("Invalid MP4 track duration")
                if track_duration <= 0 or track_duration > timescale * 60:
                    raise ValueError("MP4 track duration outside limits")
            mdia = one(track_boxes, b"mdia")
            track_media = boxes(mdia.body, mdia.end)
            media_scale, media_duration = timed_header(one(track_media, b"mdhd"), 24, 36)
            handler = one(track_media, b"hdlr")
            if handler.end - handler.body < 24:
                raise ValueError("Truncated media handler")
            if payload[handler.body + 8 : handler.body + 12] != b"vide":
                continue
            has_video = True
            # Allow ordinary encoder rounding/edit-list differences of up to one second.
            if abs(media_duration * timescale - duration * media_scale) > media_scale * timescale:
                raise ValueError("Inconsistent movie and video duration")
            minf = one(track_media, b"minf")
            stbl = one(boxes(minf.body, minf.end), b"stbl")
            stsd = one(boxes(stbl.body, stbl.end), b"stsd")
            if stsd.end - stsd.body < 8:
                raise ValueError("Missing video description")
            count = int.from_bytes(payload[stsd.body + 4 : stsd.body + 8], "big")
            samples = boxes(stsd.body + 8, stsd.end)
            if count != len(samples) or not 1 <= count <= 4:
                raise ValueError("Invalid video description count")
            for sample in samples:
                if sample.kind not in {b"avc1", b"avc3", b"hvc1", b"hev1"}:
                    raise ValueError("Unsupported video codec")
                if sample.end - sample.body < 78:
                    raise ValueError("Truncated video description")
                width = int.from_bytes(payload[sample.body + 24 : sample.body + 26], "big")
                height = int.from_bytes(payload[sample.body + 26 : sample.body + 28], "big")
                if not (1 <= width <= 4096 and 1 <= height <= 4096):
                    raise ValueError("Video dimensions outside limits")
                codec = b"avcC" if sample.kind in {b"avc1", b"avc3"} else b"hvcC"
                config = one(boxes(sample.body + 78, sample.end), codec)
                minimum = 7 if codec == b"avcC" else 23
                if config.end - config.body < minimum or payload[config.body] != 1:
                    raise ValueError("Invalid video codec configuration")
        if not has_video:
            raise ValueError("MP4 has no video track")
    except (ValueError, IndexError, OverflowError) as exc:
        raise ApiError(
            400, "shop_video_invalid", "请选择60秒以内的有效 MP4 视频（H.264 或 H.265 编码）"
        ) from exc


def prepare_shop_media(
    payload: bytes,
    kind: str,
    *,
    image_limit: int = MAX_IMAGE_BYTES,
    video_limit: int = MAX_VIDEO_BYTES,
) -> tuple[bytes, str, str, str]:
    is_video = len(payload) >= 8 and payload[4:8] == b"ftyp"
    if is_video:
        if kind != "carousel":
            raise ApiError(400, "shop_image_required", "分类和公告仅支持图片")
        if len(payload) > min(video_limit, MAX_VIDEO_BYTES):
            raise ApiError(413, "shop_media_too_large", "视频不能超过10MB，请选择较小的文件")
        validate_shop_mp4(payload)
        return payload, "video", "video/mp4", "mp4"
    if len(payload) > min(image_limit, MAX_IMAGE_BYTES):
        raise ApiError(413, "shop_media_too_large", "图片不能超过5MB，请选择较小的文件")
    encoded = prepare_shop_image(payload)
    if len(encoded) > min(image_limit, MAX_IMAGE_BYTES):
        raise ApiError(413, "shop_media_too_large", "处理后的图片超过上传限制，请选择较小的图片")
    return encoded, "image", "image/jpeg", "jpg"


def _validate_media_target(session: Session, payload: ShopMediaCreate) -> None:
    if payload.kind == "category":
        category = session.scalar(
            select(Category).where(Category.id == payload.category_id).with_for_update()
        )
        if category is None:
            raise ApiError(404, "shop_category_not_found", "分类不存在，请重新选择")
    if payload.kind == "carousel" and payload.is_active:
        count = session.scalar(
            select(func.count(ShopMedia.id)).where(
                ShopMedia.kind == "carousel", ShopMedia.is_active.is_(True)
            )
        )
        if count is not None and count >= _MAX_ACTIVE_CAROUSEL:
            raise ApiError(
                409, "shop_carousel_limit", "启用的轮播素材最多20个，请先停用或删除已有素材"
            )


def save_shop_media(
    session: Session,
    storage: ObjectStorage,
    payload: ShopMediaCreate,
    prepared: tuple[bytes, str, str, str],
    *,
    owner_customer_id: int | None = None,
    created_by: int | None = None,
) -> ShopMedia:
    encoded, media_type, mime_type, extension = prepared
    store = _locked_store(session)
    _check_owner(session, store, owner_customer_id)
    _validate_media_target(session, payload)
    object_key = f"shop/{payload.kind}/{uuid4().hex}.{extension}"
    digest = hashlib.sha256(encoded).hexdigest()
    intent = enqueue_object_cleanup(
        session,
        [object_key],
        reason="shop_media_upload_intent",
        status="intent",
        created_by=created_by,
    )[0]
    intent_id = intent.id
    session.commit()
    try:
        storage.put(object_key, encoded, content_type=mime_type, metadata={"sha256": digest})
        store = _locked_store(session)
        _check_owner(session, store, owner_customer_id)
        _validate_media_target(session, payload)
        previous = []
        if payload.is_active and payload.kind in {"category", "announcement"}:
            previous = list(
                session.scalars(
                    select(ShopMedia).where(
                        ShopMedia.kind == payload.kind,
                        ShopMedia.category_id == payload.category_id,
                        ShopMedia.is_active.is_(True),
                    )
                )
            )
        old_keys = [media.object_key for media in previous]
        for media in previous:
            media.is_active = False
        session.flush()
        media = ShopMedia(
            **payload.model_dump(),
            media_type=media_type,
            mime_type=mime_type,
            object_key=object_key,
            size_bytes=len(encoded),
            sha256=digest,
        )
        session.add(media)
        session.flush()
        # Keep old IDs present until insertion so SQLite cannot reuse a replaced URL.
        for previous_media in previous:
            session.delete(previous_media)
        intent = session.get(ObjectCleanupJob, intent_id)
        assert intent is not None
        intent.status = "completed"
        intent.completed_at = datetime.now(UTC)
        old_jobs = enqueue_object_cleanup(
            session, old_keys, reason="shop_media_replaced", created_by=created_by
        )
        session.commit()
    except Exception as exc:
        _cleanup_failed_upload(session, storage, intent_id)
        if isinstance(exc, ApiError):
            raise
        raise ApiError(503, "shop_media_unavailable", "素材暂时无法保存，请稍后重试") from exc
    run_object_cleanup_jobs(session, storage, old_jobs)
    session.refresh(media)
    return media


def _cleanup_failed_upload(session: Session, storage: ObjectStorage, intent_id: int) -> None:
    session.rollback()
    intent = session.get(ObjectCleanupJob, intent_id)
    if intent is not None and intent.status != "completed":
        intent.status = "pending"
        intent.not_before = None
        session.commit()
        run_object_cleanup_jobs(session, storage, [intent])


def serialize_media(media: ShopMedia) -> dict[str, object]:
    return {
        "id": media.id,
        "kind": media.kind,
        "category_id": media.category_id,
        "title": media.title,
        "media_type": media.media_type,
        "url": media_url(media.id),
        "sort_order": media.sort_order,
        "is_active": media.is_active,
    }


def update_shop_media(session: Session, media_id: int, payload: ShopMediaPatch) -> ShopMedia:
    _locked_store(session)
    media = session.get(ShopMedia, media_id, populate_existing=True)
    if media is None:
        raise ApiError(404, "shop_media_not_found", "素材不存在")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_active") is True and not media.is_active:
        _validate_media_target(
            session, ShopMediaCreate(kind=media.kind, category_id=media.category_id, is_active=True)
        )
        if (
            media.kind in {"category", "announcement"}
            and session.scalar(
                select(ShopMedia.id)
                .where(
                    ShopMedia.kind == media.kind,
                    ShopMedia.category_id == media.category_id,
                    ShopMedia.is_active.is_(True),
                    ShopMedia.id != media.id,
                )
                .limit(1)
            )
            is not None
        ):
            raise ApiError(409, "shop_media_active_conflict", "已有启用的素材，请先停用或删除它")
    for field, value in changes.items():
        setattr(media, field, value)
    session.commit()
    session.refresh(media)
    return media


def delete_shop_media(
    session: Session, storage: ObjectStorage, media_id: int, *, created_by: int | None = None
) -> None:
    _locked_store(session)
    media = session.get(ShopMedia, media_id, populate_existing=True)
    if media is None:
        raise ApiError(404, "shop_media_not_found", "素材不存在")
    jobs = enqueue_object_cleanup(
        session, [media.object_key], reason="shop_media_deleted", created_by=created_by
    )
    session.delete(media)
    session.commit()
    run_object_cleanup_jobs(session, storage, jobs)


def active_shop_media(session: Session, media_id: int) -> ShopMedia:
    media = session.scalar(
        select(ShopMedia).where(ShopMedia.id == media_id, ShopMedia.is_active.is_(True))
    )
    if media is not None and media.kind == "category":
        category = session.get(Category, media.category_id)
        if category is None or not category.is_active:
            media = None
    if media is None:
        raise ApiError(404, "shop_media_not_found", "素材不存在或已停用")
    return media


def read_shop_media(storage: ObjectStorage, media: ShopMedia) -> bytes:
    limit = MAX_VIDEO_BYTES if media.media_type == "video" else MAX_IMAGE_BYTES
    try:
        if not 0 < media.size_bytes <= limit:
            raise ValueError("Stored media exceeds safe limit")
        payload = storage.get(media.object_key, max_bytes=limit)
        if len(payload) != media.size_bytes or hashlib.sha256(payload).hexdigest() != media.sha256:
            raise ValueError("Stored media integrity mismatch")
    except Exception as exc:
        raise ApiError(503, "shop_media_unavailable", "素材暂时无法读取，请稍后重试") from exc
    return payload
