from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.api.dependencies import (
    AdminUser,
    DbSession,
    get_current_admin,
    require_same_origin_for_unsafe_request,
)
from app.core.errors import ApiError
from app.models import ObjectCleanupJob, Product, ProductImage
from app.schemas.catalog import (
    CategoryCreate,
    CategoryListResponse,
    CategoryRead,
    CategoryResponse,
    CategoryUpdate,
    ImageType,
    ImageUpdate,
    ImportJobListResponse,
    ImportJobRead,
    ImportJobResponse,
    ImportResultData,
    ImportResultResponse,
    ProductCreate,
    ProductListData,
    ProductListResponse,
    ProductResponse,
    ProductStatus,
    ProductUpdate,
)
from app.schemas.object_cleanup import (
    ObjectCleanupJobListResponse,
    ObjectCleanupJobRead,
    ObjectCleanupJobResponse,
)
from app.schemas.staging import StagedProductImageRead, StagedProductImageResponse
from app.services.catalog import CatalogService
from app.services.catalog_excel import create_template, export_catalog, import_catalog
from app.services.object_cleanup import (
    enqueue_object_cleanup,
    retryable_cleanup_jobs,
    run_object_cleanup_jobs,
)
from app.services.object_storage import ObjectStorage, ObjectStorageDisabledError
from app.services.product_images import ProductImageValidationError, inspect_product_image
from app.services.text_validation import validate_xml_safe_text

router = APIRouter(
    prefix="/admin",
    tags=["catalog administration"],
    dependencies=[
        Depends(get_current_admin),
        Depends(require_same_origin_for_unsafe_request),
    ],
)
catalog = CatalogService()
_STAGING_TTL = timedelta(days=7)
_STAGING_MAX_PER_PRODUCT = 100
_STAGING_MAX_GLOBAL = 5_000
_ACTIVE_CLEANUP_STATUSES = ("intent", "pending", "processing", "failed")
_MAX_CATALOG_PAGE = 1_000_000
_STAGING_QUOTA_ADVISORY_LOCK = 0x484D53544147494E


@router.get("/categories", response_model=CategoryListResponse)
def list_categories(session: DbSession) -> CategoryListResponse:
    return CategoryListResponse(
        data=[CategoryRead.model_validate(item) for item in catalog.list_categories(session)]
    )


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(payload: CategoryCreate, session: DbSession) -> CategoryResponse:
    category = catalog.create_category(session, payload)
    return CategoryResponse(data=CategoryRead.model_validate(category))


@router.patch("/categories/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    session: DbSession,
) -> CategoryResponse:
    category = catalog.update_category(session, category_id, payload)
    return CategoryResponse(data=CategoryRead.model_validate(category))


@router.delete("/categories/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, session: DbSession) -> Response:
    catalog.delete_category(session, category_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/products", response_model=ProductListResponse)
def list_products(
    session: DbSession,
    q: Annotated[str | None, Query(max_length=160)] = None,
    category_id: Annotated[int | None, Query(ge=1)] = None,
    product_status: Annotated[ProductStatus | None, Query(alias="status")] = None,
    page: Annotated[int, Query(ge=1, le=_MAX_CATALOG_PAGE)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ProductListResponse:
    products, total = catalog.list_products(
        session,
        page=page,
        page_size=page_size,
        query_text=q,
        category_id=category_id,
        status=product_status,
    )
    return ProductListResponse(
        data=ProductListData(
            items=[catalog.serialize_product(product) for product in products],
            total=total,
            page=page,
            page_size=page_size,
        )
    )


@router.post(
    "/products",
    response_model=ProductResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_product(payload: ProductCreate, session: DbSession) -> ProductResponse:
    return ProductResponse(data=catalog.serialize_product(catalog.create_product(session, payload)))


@router.get("/products/template.xlsx")
def download_product_template(session: DbSession) -> Response:
    payload = create_template(catalog.list_categories(session))
    return _xlsx_response(payload, "harbor-market-product-template.xlsx")


@router.get("/products/export.xlsx")
def export_products(session: DbSession) -> Response:
    return _xlsx_response(export_catalog(session), "harbor-market-products.xlsx")


@router.post("/product-images/staging", response_model=StagedProductImageResponse)
def upload_staged_product_image(
    request: Request,
    session: DbSession,
    admin: AdminUser,
    file: Annotated[UploadFile, File()],
    product_code: Annotated[str, Form(min_length=1, max_length=64)],
) -> StagedProductImageResponse:
    normalized_code = product_code.strip().upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]*", normalized_code):
        raise ApiError(422, "invalid_product_code", "Product code format is invalid")
    payload = file.file.read(request.app.state.settings.upload_max_bytes + 1)
    if len(payload) > request.app.state.settings.upload_max_bytes:
        raise ApiError(413, "image_too_large", "Image exceeds the configured upload limit")
    mime_type, extension, width, height = _inspect_image(payload)
    _lock_staging_quota(session)
    active_staging = select(ObjectCleanupJob.id).where(
        ObjectCleanupJob.reason == "staging_expiry",
        ObjectCleanupJob.status.in_(_ACTIVE_CLEANUP_STATUSES),
    )
    if len(list(session.scalars(active_staging.limit(_STAGING_MAX_GLOBAL + 1)))) >= (
        _STAGING_MAX_GLOBAL
    ):
        raise ApiError(409, "staging_quota_exceeded", "Global staging quota is full")
    product_prefix = f"products/staged/{normalized_code}/"
    per_product = active_staging.where(
        ObjectCleanupJob.object_key.startswith(product_prefix, autoescape=True)
    )
    if len(list(session.scalars(per_product.limit(_STAGING_MAX_PER_PRODUCT + 1)))) >= (
        _STAGING_MAX_PER_PRODUCT
    ):
        raise ApiError(409, "staging_quota_exceeded", "Product staging quota is full")

    object_key = f"products/staged/{normalized_code}/{uuid4().hex}.{extension}"
    expires_at = datetime.now(UTC) + _STAGING_TTL
    expiry_job = enqueue_object_cleanup(
        session,
        [object_key],
        reason="staging_expiry",
        not_before=expires_at,
        created_by=admin.id,
    )[0]
    session.commit()
    try:
        request.app.state.object_storage.put(
            object_key,
            payload,
            content_type=mime_type,
            metadata={"sha256": hashlib.sha256(payload).hexdigest()},
        )
    except ObjectStorageDisabledError as exc:
        failed = _run_upload_intent_cleanup(
            session,
            request.app.state.object_storage,
            expiry_job.id,
        )
        if failed:
            raise _cleanup_pending_error(failed) from exc
        raise ApiError(503, "storage_unavailable", "Object storage is not configured") from exc
    except Exception as exc:
        failed = _run_upload_intent_cleanup(
            session,
            request.app.state.object_storage,
            expiry_job.id,
        )
        if failed:
            raise _cleanup_pending_error(failed) from exc
        raise ApiError(503, "storage_unavailable", "Image could not be staged") from exc
    return StagedProductImageResponse(
        data=StagedProductImageRead(
            object_key=object_key,
            mime_type=mime_type,
            size_bytes=len(payload),
            width=width,
            height=height,
            expires_at=expires_at,
        )
    )


@router.delete(
    "/product-images/staging/{object_key:path}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_staged_product_image(
    object_key: str,
    request: Request,
    session: DbSession,
) -> Response:
    if not re.fullmatch(
        r"products/staged/[A-Z0-9][A-Z0-9_.-]{0,63}/[0-9a-f]{32}\.(?:jpg|png|webp)",
        object_key,
    ):
        raise ApiError(422, "invalid_staging_key", "Staging object key is invalid")
    if session.scalar(
        select(ProductImage.id).where(ProductImage.object_key == object_key).limit(1)
    ):
        raise ApiError(409, "staging_key_in_use", "Staging object is attached to a product")
    now = datetime.now(UTC)
    scheduled_jobs = list(
        session.scalars(
            select(ObjectCleanupJob).where(
                ObjectCleanupJob.object_key == object_key,
                ObjectCleanupJob.reason == "staging_expiry",
                ObjectCleanupJob.status != "completed",
            )
        )
    )
    for job in scheduled_jobs:
        job.status = "completed"
        job.completed_at = now
        job.last_error = None
    cleanup_jobs = enqueue_object_cleanup(
        session,
        [object_key],
        reason="staging_cancelled",
    )
    session.commit()
    failed = run_object_cleanup_jobs(
        session,
        request.app.state.object_storage,
        cleanup_jobs,
    )
    if failed:
        raise _cleanup_pending_error(failed)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/products/import", response_model=ImportResultResponse)
def import_products(
    request: Request,
    session: DbSession,
    admin: AdminUser,
    file: Annotated[UploadFile, File()],
    dry_run: Annotated[bool, Query()] = True,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="X-Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$",
        ),
    ] = None,
) -> ImportResultResponse:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise ApiError(422, "invalid_excel", "Only .xlsx workbooks are supported")
    limit = 10 * 1024 * 1024
    # A synchronous endpoint is intentionally used: FastAPI runs it in its worker
    # pool, so workbook parsing and bounded MinIO validation never block the event loop.
    payload = file.file.read(limit + 1)
    if len(payload) > limit:
        raise ApiError(413, "excel_too_large", "Excel workbook exceeds the 10 MiB limit")
    job = import_catalog(
        session,
        user=admin,
        filename=file.filename or "products.xlsx",
        payload=payload,
        dry_run=dry_run,
        storage=request.app.state.object_storage,
        image_max_bytes=request.app.state.settings.upload_max_bytes,
        idempotency_key=idempotency_key,
    )
    return ImportResultResponse(
        data=ImportResultData(
            job_id=job.id,
            dry_run=job.dry_run,
            valid=job.status in {"validated", "completed"},
            summary={key: int(value) for key, value in job.summary.items()},
            errors=job.errors,
        )
    )


@router.get("/import-jobs/{job_id}", response_model=ImportJobResponse)
def get_import_job(job_id: int, session: DbSession) -> ImportJobResponse:
    from app.models import ImportJob

    job = session.get(ImportJob, job_id)
    if job is None:
        raise ApiError(404, "import_job_not_found", "Import job was not found")
    return ImportJobResponse(data=ImportJobRead.model_validate(job))


@router.get("/import-jobs", response_model=ImportJobListResponse)
def list_import_jobs(
    session: DbSession,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ImportJobListResponse:
    from app.models import ImportJob

    jobs = list(
        session.scalars(
            select(ImportJob)
            .where(ImportJob.created_by == admin.id)
            .order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
            .limit(limit)
        )
    )
    return ImportJobListResponse(data=[ImportJobRead.model_validate(job) for job in jobs])


@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, session: DbSession) -> ProductResponse:
    return ProductResponse(data=catalog.serialize_product(catalog.get_product(session, product_id)))


@router.patch("/products/{product_id}", response_model=ProductResponse)
def update_product(
    product_id: int,
    payload: ProductUpdate,
    session: DbSession,
) -> ProductResponse:
    product = catalog.update_product(session, product_id, payload)
    return ProductResponse(data=catalog.serialize_product(product))


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, session: DbSession, request: Request) -> Response:
    cleanup_jobs = catalog.delete_product(session, product_id)
    failed = run_object_cleanup_jobs(session, request.app.state.object_storage, cleanup_jobs)
    if failed:
        raise _cleanup_pending_error(failed)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/products/{product_id}/images", response_model=ProductResponse)
def upload_product_image(
    product_id: int,
    request: Request,
    session: DbSession,
    file: Annotated[UploadFile, File()],
    image_type: Annotated[ImageType, Form()],
    alt_text: Annotated[str | None, Form(max_length=200)] = None,
    sort_order: Annotated[int, Form(ge=-(2**31), le=2**31 - 1)] = 0,
) -> ProductResponse:
    payload = file.file.read(request.app.state.settings.upload_max_bytes + 1)
    if len(payload) > request.app.state.settings.upload_max_bytes:
        raise ApiError(413, "image_too_large", "Image exceeds the configured upload limit")
    mime_type, extension, width, height = _inspect_image(payload)
    _validate_alt_text(alt_text)
    preliminary_product = catalog.get_product(session, product_id)
    catalog.validate_image_capacity(preliminary_product, image_type)
    object_key = f"products/{product_id}/{image_type}/{uuid4().hex}.{extension}"
    cleanup_intent = enqueue_object_cleanup(
        session,
        [object_key],
        reason="image_upload_intent",
        status="intent",
    )[0]
    session.commit()
    storage = request.app.state.object_storage
    try:
        storage.put(
            object_key,
            payload,
            content_type=mime_type,
            metadata={"sha256": hashlib.sha256(payload).hexdigest()},
        )
    except ObjectStorageDisabledError as exc:
        failed = _run_upload_intent_cleanup(session, storage, cleanup_intent.id)
        if failed:
            raise _cleanup_pending_error(failed) from exc
        raise ApiError(503, "storage_unavailable", "Object storage is not configured") from exc
    except Exception as exc:
        failed = _run_upload_intent_cleanup(session, storage, cleanup_intent.id)
        if failed:
            raise _cleanup_pending_error(failed) from exc
        raise ApiError(503, "storage_unavailable", "Image could not be stored") from exc

    try:
        locked_id = session.scalar(
            select(Product.id).where(Product.id == product_id).with_for_update()
        )
        if locked_id is None:
            raise ApiError(404, "product_not_found", "Product was not found")
        # The preflight collection was loaded before the durable upload intent
        # commit. Refresh after taking the product row lock so a concurrent
        # uploader's committed image participates in the authoritative limit.
        session.expire_all()
        product = catalog.get_product(session, product_id)
        catalog.validate_image_capacity(product, image_type)
        image = ProductImage(
            product=product,
            object_key=object_key,
            image_type=image_type,
            alt_text=alt_text or product.name,
            sort_order=sort_order,
            mime_type=mime_type,
            size_bytes=len(payload),
            width=width,
            height=height,
        )
        session.add(image)
        cleanup_intent = session.get(ObjectCleanupJob, cleanup_intent.id)
        assert cleanup_intent is not None
        cleanup_intent.status = "completed"
        cleanup_intent.completed_at = datetime.now(UTC)
        session.commit()
    except ApiError as exc:
        failed = _run_upload_intent_cleanup(session, storage, cleanup_intent.id)
        if failed:
            raise _cleanup_pending_error(failed) from exc
        raise
    except IntegrityError as exc:
        failed = _run_upload_intent_cleanup(session, storage, cleanup_intent.id)
        if failed:
            raise _cleanup_pending_error(failed) from exc
        raise ApiError(409, "image_conflict", "Image could not be attached") from exc
    except Exception as exc:
        failed = _run_upload_intent_cleanup(session, storage, cleanup_intent.id)
        if failed:
            raise _cleanup_pending_error(failed) from exc
        raise
    return ProductResponse(data=catalog.serialize_product(catalog.get_product(session, product_id)))


@router.patch(
    "/products/{product_id}/images/{image_id}",
    response_model=ProductResponse,
)
def update_product_image(
    product_id: int,
    image_id: int,
    payload: ImageUpdate,
    session: DbSession,
) -> ProductResponse:
    catalog._lock_product(session, product_id)
    product = catalog.get_product(session, product_id)
    image = next((item for item in product.images if item.id == image_id), None)
    if image is None:
        raise ApiError(404, "image_not_found", "Product image was not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(image, field, value)
    session.commit()
    return ProductResponse(data=catalog.serialize_product(catalog.get_product(session, product_id)))


@router.delete(
    "/products/{product_id}/images/{image_id}",
    response_model=ProductResponse,
)
def delete_product_image(
    product_id: int,
    image_id: int,
    request: Request,
    session: DbSession,
) -> ProductResponse:
    catalog._lock_product(session, product_id)
    product = catalog.get_product(session, product_id)
    image = next((item for item in product.images if item.id == image_id), None)
    if image is None:
        raise ApiError(404, "image_not_found", "Product image was not found")
    if product.status == "published" and image.image_type == "cover":
        raise ApiError(409, "published_cover", "Unpublish the product before deleting its cover")
    object_key = image.object_key
    cleanup_jobs = enqueue_object_cleanup(
        session,
        [object_key],
        reason="image_deleted",
    )
    session.delete(image)
    session.commit()
    session.expire(product, ["images"])
    failed = run_object_cleanup_jobs(
        session,
        request.app.state.object_storage,
        cleanup_jobs,
    )
    if failed:
        raise _cleanup_pending_error(failed)
    return ProductResponse(data=catalog.serialize_product(catalog.get_product(session, product_id)))


@router.get("/object-cleanup-jobs", response_model=ObjectCleanupJobListResponse)
def list_object_cleanup_jobs(
    session: DbSession,
    cleanup_status: Annotated[str | None, Query(alias="status")] = None,
) -> ObjectCleanupJobListResponse:
    statement = select(ObjectCleanupJob).order_by(
        ObjectCleanupJob.created_at.desc(),
        ObjectCleanupJob.id.desc(),
    )
    if cleanup_status is not None:
        if cleanup_status not in {"intent", "pending", "processing", "completed", "failed"}:
            raise ApiError(422, "invalid_cleanup_status", "Cleanup status is invalid")
        statement = statement.where(ObjectCleanupJob.status == cleanup_status)
    jobs = list(session.scalars(statement.limit(200)))
    return ObjectCleanupJobListResponse(
        data=[ObjectCleanupJobRead.model_validate(job) for job in jobs]
    )


@router.post(
    "/object-cleanup-jobs/{job_id}/retry",
    response_model=ObjectCleanupJobResponse,
)
def retry_object_cleanup_job(
    job_id: int,
    request: Request,
    session: DbSession,
) -> ObjectCleanupJobResponse:
    job = session.get(ObjectCleanupJob, job_id)
    if job is None:
        raise ApiError(404, "cleanup_job_not_found", "Object cleanup job was not found")
    if job.status != "completed":
        failed = run_object_cleanup_jobs(
            session,
            request.app.state.object_storage,
            retryable_cleanup_jobs(session, job_id=job_id, limit=1),
        )
        if failed:
            raise _cleanup_pending_error(failed)
        job = session.get(ObjectCleanupJob, job_id)
        assert job is not None
    return ObjectCleanupJobResponse(data=ObjectCleanupJobRead.model_validate(job))


def _inspect_image(payload: bytes) -> tuple[str, str, int, int]:
    try:
        inspection = inspect_product_image(payload)
    except ProductImageValidationError as exc:
        raise ApiError(422, exc.code, exc.message) from exc
    return (
        inspection.mime_type,
        inspection.extension,
        inspection.width,
        inspection.height,
    )


def _validate_alt_text(alt_text: str | None) -> None:
    if alt_text is None:
        return
    try:
        validate_xml_safe_text(alt_text, label="Image alt text", max_length=200)
    except ValueError as exc:
        raise ApiError(422, "invalid_alt_text", str(exc)) from exc


def _cleanup_pending_error(failed: list[ObjectCleanupJob]) -> ApiError:
    job_ids = ",".join(str(job.id) for job in failed)
    return ApiError(
        503,
        "cleanup_pending",
        f"Database change completed; object cleanup remains retryable in job(s): {job_ids}",
    )


def _run_upload_intent_cleanup(
    session: DbSession,
    storage: ObjectStorage,
    job_id: int,
) -> list[ObjectCleanupJob]:
    session.rollback()
    job = session.get(ObjectCleanupJob, job_id)
    if job is None or job.status == "completed":
        return []
    job.status = "pending"
    job.not_before = None
    session.commit()
    return run_object_cleanup_jobs(session, storage, [job])


def _xlsx_response(payload: bytes, filename: str) -> Response:
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _lock_staging_quota(session: DbSession) -> None:
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _STAGING_QUOTA_ADVISORY_LOCK},
        )
