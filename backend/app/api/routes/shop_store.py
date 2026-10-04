from __future__ import annotations

import re
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import ValidationError
from sqlalchemy import select

from app.api.dependencies import (
    AdminUser,
    DbSession,
    get_current_admin,
    require_same_origin_for_unsafe_request,
)
from app.api.routes.mini_auth import MiniAuthentication
from app.core.errors import ApiError
from app.models import ShopMedia
from app.schemas.shop_store import (
    AdminStorePatch,
    AdminStoreRead,
    AdminStoreResponse,
    MiniStoreMeData,
    MiniStoreMeResponse,
    ShopMediaCreate,
    ShopMediaDeletedData,
    ShopMediaDeletedResponse,
    ShopMediaKind,
    ShopMediaListResponse,
    ShopMediaPatch,
    ShopMediaRead,
    ShopMediaResponse,
    StorePatch,
    StoreRead,
    StoreResponse,
)
from app.services.shop_store import (
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    active_shop_media,
    admin_store,
    can_manage_store,
    delete_shop_media,
    prepare_shop_media,
    public_store,
    read_shop_media,
    require_store_owner,
    save_shop_media,
    serialize_media,
    update_shop_media,
    update_store,
)

admin_router = APIRouter(
    prefix="/admin/shop",
    tags=["门店管理"],
    dependencies=[
        Depends(get_current_admin),
        Depends(require_same_origin_for_unsafe_request),
    ],
)
mini_router = APIRouter(prefix="/mini/shop", tags=["小程序门店管理"])
public_router = APIRouter(prefix="/shop", tags=["门店素材"])


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization, Cookie"


def _prepared(request: Request, file: UploadFile, kind: str) -> tuple[bytes, str, str, str]:
    settings = request.app.state.settings
    image_limit = min(settings.shop_image_upload_max_bytes, MAX_IMAGE_BYTES)
    video_limit = min(settings.shop_video_upload_max_bytes, MAX_VIDEO_BYTES)
    limit = max(image_limit, video_limit) if kind == "carousel" else image_limit
    payload = file.file.read(limit + 1)
    if len(payload) > limit:
        raise ApiError(413, "shop_media_too_large", "素材文件超过上传限制，请选择较小的文件")
    return prepare_shop_media(
        payload,
        kind,
        image_limit=settings.shop_image_upload_max_bytes,
        video_limit=settings.shop_video_upload_max_bytes,
    )


@admin_router.get("/store", response_model=AdminStoreResponse)
def read_admin_store(session: DbSession, response: Response) -> AdminStoreResponse:
    _no_store(response)
    return AdminStoreResponse(data=AdminStoreRead(**admin_store(session)))


@admin_router.patch("/store", response_model=AdminStoreResponse)
def patch_admin_store(
    payload: AdminStorePatch, session: DbSession, response: Response
) -> AdminStoreResponse:
    update_store(session, payload)
    _no_store(response)
    return AdminStoreResponse(data=AdminStoreRead(**admin_store(session)))


@admin_router.get("/media", response_model=ShopMediaListResponse)
def list_admin_media(
    session: DbSession,
    response: Response,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ShopMediaListResponse:
    media = session.scalars(
        select(ShopMedia)
        .order_by(ShopMedia.sort_order, ShopMedia.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    _no_store(response)
    return ShopMediaListResponse(data=[ShopMediaRead(**serialize_media(item)) for item in media])


@admin_router.post("/media", response_model=ShopMediaResponse, status_code=status.HTTP_201_CREATED)
def upload_admin_media(
    request: Request,
    response: Response,
    session: DbSession,
    admin: AdminUser,
    file: Annotated[UploadFile, File()],
    kind: Annotated[ShopMediaKind, Form()],
    title: Annotated[str, Form(max_length=160)] = "",
    category_id: Annotated[int | None, Form(ge=1, le=2**31 - 1)] = None,
    sort_order: Annotated[int, Form(ge=-(2**31), le=2**31 - 1)] = 0,
    is_active: Annotated[bool, Form()] = True,
) -> ShopMediaResponse:
    try:
        payload = ShopMediaCreate(
            kind=kind,
            title=title,
            category_id=category_id,
            sort_order=sort_order,
            is_active=is_active,
        )
    except ValidationError as exc:
        raise ApiError(422, "shop_media_invalid", "素材参数无效，请检查分类和标题后重试") from exc
    media = save_shop_media(
        session,
        request.app.state.object_storage,
        payload,
        _prepared(request, file, kind),
        created_by=admin.id,
    )
    _no_store(response)
    return ShopMediaResponse(data=ShopMediaRead(**serialize_media(media)))


@admin_router.patch("/media/{media_id}", response_model=ShopMediaResponse)
def patch_admin_media(
    media_id: Annotated[int, Path(ge=1, le=2**31 - 1)],
    payload: ShopMediaPatch,
    session: DbSession,
    response: Response,
) -> ShopMediaResponse:
    media = update_shop_media(session, media_id, payload)
    _no_store(response)
    return ShopMediaResponse(data=ShopMediaRead(**serialize_media(media)))


@admin_router.delete("/media/{media_id}", response_model=ShopMediaDeletedResponse)
def remove_admin_media(
    media_id: Annotated[int, Path(ge=1, le=2**31 - 1)],
    request: Request,
    response: Response,
    session: DbSession,
    admin: AdminUser,
) -> ShopMediaDeletedResponse:
    delete_shop_media(session, request.app.state.object_storage, media_id, created_by=admin.id)
    _no_store(response)
    return ShopMediaDeletedResponse(data=ShopMediaDeletedData())


@mini_router.get("/me", response_model=MiniStoreMeResponse)
def mini_store_me(
    identity: MiniAuthentication, session: DbSession, response: Response
) -> MiniStoreMeResponse:
    _no_store(response)
    return MiniStoreMeResponse(
        data=MiniStoreMeData(can_manage_store=can_manage_store(session, identity[0].id))
    )


@mini_router.get("/store", response_model=StoreResponse)
def read_mini_store(
    identity: MiniAuthentication, session: DbSession, response: Response
) -> StoreResponse:
    require_store_owner(session, identity[0].id)
    _no_store(response)
    return StoreResponse(data=StoreRead(**public_store(session)))


@mini_router.patch("/store", response_model=StoreResponse)
def patch_mini_store(
    payload: StorePatch, identity: MiniAuthentication, session: DbSession, response: Response
) -> StoreResponse:
    require_store_owner(session, identity[0].id)
    update_store(session, payload, owner_customer_id=identity[0].id)
    _no_store(response)
    return StoreResponse(data=StoreRead(**public_store(session)))


@mini_router.post("/store/announcement", response_model=StoreResponse)
def upload_mini_announcement(
    identity: MiniAuthentication,
    request: Request,
    response: Response,
    session: DbSession,
    file: Annotated[UploadFile, File()],
) -> StoreResponse:
    require_store_owner(session, identity[0].id)
    save_shop_media(
        session,
        request.app.state.object_storage,
        ShopMediaCreate(kind="announcement"),
        _prepared(request, file, "announcement"),
        owner_customer_id=identity[0].id,
    )
    _no_store(response)
    return StoreResponse(data=StoreRead(**public_store(session)))


def _byte_range(value: str, total: int, headers: dict[str, str]) -> tuple[int, int]:
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value) if len(value) <= 100 else None
    try:
        if match is None or not any(match.groups()):
            raise ValueError("Invalid range")
        start_text, end_text = match.groups()
        if not start_text:
            suffix = int(end_text)
            if suffix <= 0:
                raise ValueError("Invalid suffix")
            return max(total - suffix, 0), total - 1
        start = int(start_text)
        end = min(int(end_text), total - 1) if end_text else total - 1
        if start >= total or end < start:
            raise ValueError("Unsatisfiable range")
        return start, end
    except ValueError as exc:
        raise ApiError(
            416,
            "shop_media_range_invalid",
            "视频请求范围无效",
            headers={**headers, "Content-Range": f"bytes */{total}"},
        ) from exc


@public_router.get("/media/{media_id}", response_class=Response)
def public_media(
    media_id: Annotated[int, Path(ge=1, le=2**31 - 1)], request: Request, session: DbSession
) -> Response:
    media = active_shop_media(session, media_id)
    payload = read_shop_media(request.app.state.object_storage, media)
    headers = {
        "Cache-Control": "public, max-age=0, must-revalidate",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
        "ETag": f'"{media.sha256}"',
    }
    status_code = 200
    if media.media_type == "video":
        headers["Accept-Ranges"] = "bytes"
        byte_range = request.headers.get("range")
        if byte_range is not None:
            start, end = _byte_range(byte_range, len(payload), headers)
            headers["Content-Range"] = f"bytes {start}-{end}/{len(payload)}"
            payload = payload[start : end + 1]
            status_code = 206
    return Response(
        content=payload, media_type=media.mime_type, headers=headers, status_code=status_code
    )
