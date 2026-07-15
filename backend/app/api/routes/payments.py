from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response, status
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import AdminUser, DbSession, require_same_origin_for_unsafe_request
from app.core.errors import ApiError
from app.payments.domain import ProviderTradeState
from app.schemas.payments import (
    MockPaymentCreateRequest,
    MockProviderStateRequest,
    PaymentAttemptDetail,
    PaymentAttemptDetailResponse,
    PaymentAttemptPublic,
    PaymentAttemptResponse,
)
from app.services.payments import PaymentService

admin_router = APIRouter(
    prefix="/admin/payments",
    tags=["admin-payments"],
    dependencies=[Depends(require_same_origin_for_unsafe_request)],
)
provider_router = APIRouter(prefix="/payments/providers/wechat-pay", tags=["payments"])


@admin_router.post(
    "",
    response_model=PaymentAttemptResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_mock_payment(
    payload: MockPaymentCreateRequest,
    request: Request,
    session: DbSession,
    admin: AdminUser,
    idempotency_key: Annotated[
        str | None,
        Header(alias="X-Idempotency-Key"),
    ] = None,
) -> PaymentAttemptResponse:
    if idempotency_key is None:
        raise ApiError(
            422,
            "idempotency_key_required",
            "X-Idempotency-Key is required",
        )
    attempt = _service(request, session).create_mock_attempt(
        owner_user_id=admin.id,
        request=payload,
        idempotency_key=idempotency_key,
    )
    return PaymentAttemptResponse(data=PaymentAttemptPublic.model_validate(attempt))


@admin_router.get("/{public_id}", response_model=PaymentAttemptDetailResponse)
def get_payment(
    public_id: str,
    request: Request,
    session: DbSession,
    _admin: AdminUser,
) -> PaymentAttemptDetailResponse:
    attempt = _service(request, session).get_attempt(public_id, include_events=True)
    return PaymentAttemptDetailResponse(data=PaymentAttemptDetail.model_validate(attempt))


@admin_router.post("/{public_id}/reconcile", response_model=PaymentAttemptResponse)
def reconcile_payment(
    public_id: str,
    request: Request,
    session: DbSession,
    _admin: AdminUser,
) -> PaymentAttemptResponse:
    attempt = _service(request, session).reconcile(public_id)
    return PaymentAttemptResponse(data=PaymentAttemptPublic.model_validate(attempt))


@admin_router.post("/{public_id}/refresh-prepay", response_model=PaymentAttemptResponse)
def refresh_payment_prepay(
    public_id: str,
    request: Request,
    session: DbSession,
    _admin: AdminUser,
) -> PaymentAttemptResponse:
    attempt = _service(request, session).refresh_prepay(public_id)
    return PaymentAttemptResponse(data=PaymentAttemptPublic.model_validate(attempt))


@admin_router.post("/{public_id}/close", response_model=PaymentAttemptResponse)
def close_payment(
    public_id: str,
    request: Request,
    session: DbSession,
    _admin: AdminUser,
) -> PaymentAttemptResponse:
    attempt = _service(request, session).close(public_id)
    return PaymentAttemptResponse(data=PaymentAttemptPublic.model_validate(attempt))


@admin_router.post(
    "/{public_id}/mock/provider-state",
    response_model=PaymentAttemptResponse,
)
def set_mock_provider_state(
    public_id: str,
    payload: MockProviderStateRequest,
    request: Request,
    session: DbSession,
    _admin: AdminUser,
) -> PaymentAttemptResponse:
    attempt = _service(request, session).simulate_provider_state(
        public_id,
        trade_state=ProviderTradeState(payload.trade_state),
        deliver_callback=payload.deliver_callback,
        provider_event_id=payload.provider_event_id,
    )
    return PaymentAttemptResponse(data=PaymentAttemptPublic.model_validate(attempt))


@provider_router.post("/notify", status_code=status.HTTP_204_NO_CONTENT)
async def receive_payment_notification(
    request: Request,
) -> Response:
    max_bytes = request.app.state.settings.payment_webhook_max_bytes
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared_size = int(content_length)
        except ValueError:
            declared_size = 0
        if declared_size > max_bytes:
            raise ApiError(413, "payment_notification_too_large", "Notification body is too large")

    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_bytes:
            raise ApiError(413, "payment_notification_too_large", "Notification body is too large")
        body.extend(chunk)

    raw_body = bytes(body)
    headers = dict(request.headers)
    settings = request.app.state.settings
    gateway = request.app.state.payment_gateway
    session_factory = request.app.state.session_factory

    def process() -> None:
        # Construct and consume the synchronous SQLAlchemy session on the same
        # worker thread so row-lock waits never block FastAPI's event loop.
        with session_factory() as session:
            PaymentService(session, settings, gateway).process_notification(raw_body, headers)

    await run_in_threadpool(process)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _service(request: Request, session: DbSession) -> PaymentService:
    return PaymentService(
        session,
        request.app.state.settings,
        request.app.state.payment_gateway,
    )
