from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.api.dependencies import DbSession, OptionalCurrentUser
from app.core.errors import ApiError
from app.models import Category, Product, ProductImage
from app.schemas.catalog import (
    CategoryListResponse,
    CategoryRead,
    ProductListData,
    ProductListResponse,
    ProductResponse,
)
from app.services.catalog import CatalogService
from app.services.object_storage import (
    ObjectStorageDisabledError,
    ObjectStorageNotFoundError,
    ObjectStorageSizeError,
)

router = APIRouter(prefix="/catalog", tags=["public catalog"])
media_router = APIRouter(prefix="/media", tags=["product media"])
catalog = CatalogService()
logger = logging.getLogger(__name__)
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_MAX_CATALOG_PAGE = 1_000_000


@router.get("/categories", response_model=CategoryListResponse)
def list_categories(session: DbSession) -> CategoryListResponse:
    return CategoryListResponse(
        data=[
            CategoryRead.model_validate(category)
            for category in catalog.list_categories(session, active_only=True)
        ]
    )


@router.get("/products", response_model=ProductListResponse)
def list_products(
    session: DbSession,
    q: Annotated[str | None, Query(max_length=160)] = None,
    category: Annotated[str | None, Query(max_length=50)] = None,
    page: Annotated[int, Query(ge=1, le=_MAX_CATALOG_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProductListResponse:
    products, total = catalog.list_products(
        session,
        page=page,
        page_size=page_size,
        query_text=q,
        category_code=category,
        public_only=True,
    )
    return ProductListResponse(
        data=ProductListData(
            items=[catalog.serialize_product(product, public_only=True) for product in products],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.get("/products/{product_code}", response_model=ProductResponse)
def get_product(product_code: str, session: DbSession) -> ProductResponse:
    product = catalog.get_product_by_code(session, product_code, public_only=True)
    return ProductResponse(data=catalog.serialize_product(product, public_only=True))


@media_router.get("/{object_key:path}")
def get_product_media(
    object_key: str,
    request: Request,
    session: DbSession,
    user: OptionalCurrentUser,
) -> Response:
    known = session.execute(
        select(
            ProductImage.id,
            ProductImage.mime_type,
            ProductImage.size_bytes,
            Product.status,
            Category.is_active,
        )
        .join(ProductImage.product)
        .join(Product.category)
        .where(ProductImage.object_key == object_key)
        .limit(1)
    ).one_or_none()
    is_public = bool(known is not None and known.status == "published" and known.is_active)
    if known is None or (not is_public and not (user and user.is_admin)):
        raise ApiError(404, "media_not_found", "Media was not found")
    storage = request.app.state.object_storage
    if known.mime_type not in _IMAGE_MIME_TYPES or known.size_bytes is None:
        logger.error("Media metadata is invalid for object %s", object_key)
        raise ApiError(503, "media_integrity_error", "Media metadata is invalid")
    try:
        object_stat = storage.stat(object_key)
        payload = storage.get(
            object_key,
            max_bytes=request.app.state.settings.upload_max_bytes,
        )
    except ObjectStorageDisabledError as exc:
        raise ApiError(503, "storage_unavailable", "Object storage is not configured") from exc
    except ObjectStorageSizeError as exc:
        logger.error("Media object exceeds its configured limit: %s", object_key)
        raise ApiError(503, "media_integrity_error", "Stored media failed validation") from exc
    except ObjectStorageNotFoundError as exc:
        raise ApiError(404, "media_not_found", "Media was not found") from exc
    except Exception as exc:
        logger.exception("Object storage failed while reading media %s", object_key)
        raise ApiError(503, "storage_unavailable", "Object storage is unavailable") from exc
    if object_stat.size != known.size_bytes or len(payload) != known.size_bytes:
        logger.error("Media size metadata mismatch for object %s", object_key)
        raise ApiError(503, "media_integrity_error", "Stored media failed validation")
    return Response(
        content=payload,
        media_type=known.mime_type,
        headers={
            "Cache-Control": (
                "public, max-age=0, must-revalidate" if is_public else "private, no-store"
            ),
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
