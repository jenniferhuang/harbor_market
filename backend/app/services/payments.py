from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.errors import ApiError
from app.models import PaymentAttempt, PaymentProviderEvent, PaymentStateEvent
from app.payments.domain import (
    PaymentStatus,
    ProviderTradeState,
    TransitionAction,
    TransitionDecision,
    decide_transition,
    status_for_provider_state,
)
from app.payments.providers.base import (
    PaymentGateway,
    PaymentGatewayError,
    PaymentNotificationError,
    PaymentSnapshot,
    PrepayRequest,
)
from app.payments.providers.mock_wechat import MockWeChatPayGateway
from app.schemas.payments import MockPaymentCreateRequest

_IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class PaymentService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        gateway: PaymentGateway | None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.gateway = gateway
        self._now = now or (lambda: datetime.now(UTC))

    def create_mock_attempt(
        self,
        *,
        owner_user_id: int,
        request: MockPaymentCreateRequest,
        idempotency_key: str,
    ) -> PaymentAttempt:
        gateway = self._require_mock_controls()
        key = idempotency_key.strip()
        if not _IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
            raise ApiError(
                422,
                "invalid_idempotency_key",
                "X-Idempotency-Key must be 8-128 safe ASCII characters",
            )
        request_hash = _request_hash(request, gateway)
        existing = self.session.scalar(
            select(PaymentAttempt).where(
                PaymentAttempt.owner_user_id == owner_user_id,
                PaymentAttempt.idempotency_key == key,
            )
        )
        if existing is not None:
            self._assert_same_idempotent_request(existing, request_hash)
            return self._ensure_prepay(existing.public_id)

        prior_attempts = self._lock_order_attempts(request.order_reference)
        self._validate_new_attempt(owner_user_id, request, prior_attempts)
        now = self._now()
        attempt = PaymentAttempt(
            public_id=str(uuid4()),
            owner_user_id=owner_user_id,
            order_reference=request.order_reference,
            merchant_order_no=_merchant_order_no(now),
            provider=gateway.provider_code,
            provider_mode=gateway.mode,
            provider_app_id=gateway.app_id,
            provider_merchant_id=gateway.merchant_id,
            status=PaymentStatus.CREATED.value,
            amount_cents=request.amount_cents,
            currency=request.currency,
            description=request.description,
            idempotency_key=key,
            request_hash=request_hash,
            client_parameters={},
        )
        self.session.add(attempt)
        self._record_event(
            attempt,
            event_type="payment.attempt_created",
            source="api",
            from_status=None,
            to_status=PaymentStatus.CREATED,
            reason_code="mock_admin_request",
            details={"order_reference": request.order_reference},
        )
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raced = self.session.scalar(
                select(PaymentAttempt).where(
                    PaymentAttempt.owner_user_id == owner_user_id,
                    PaymentAttempt.idempotency_key == key,
                )
            )
            if raced is not None:
                self._assert_same_idempotent_request(raced, request_hash)
                return self._ensure_prepay(raced.public_id)
            raise ApiError(
                409,
                "payment_attempt_conflict",
                "Another payment attempt is already active for this order",
            ) from exc
        return self._ensure_prepay(attempt.public_id)

    def get_attempt(self, public_id: str, *, include_events: bool = False) -> PaymentAttempt:
        statement = select(PaymentAttempt).where(PaymentAttempt.public_id == public_id)
        if include_events:
            statement = statement.options(selectinload(PaymentAttempt.events))
        attempt = self.session.scalar(statement)
        if attempt is None:
            raise ApiError(404, "payment_not_found", "Payment attempt was not found")
        return attempt

    def reconcile(self, public_id: str) -> PaymentAttempt:
        gateway = self._require_gateway()
        attempt = self._lock_attempt(public_id)
        self._assert_gateway_matches_attempt(attempt, gateway)
        try:
            snapshot = gateway.query_payment(attempt.merchant_order_no)
        except PaymentGatewayError as exc:
            raise _gateway_api_error(exc) from exc
        self._apply_snapshot(attempt, snapshot, source="provider_query")
        self.session.commit()
        if (
            PaymentStatus(attempt.status) == PaymentStatus.CREATED
            and snapshot.trade_state == ProviderTradeState.NOTPAY
        ):
            return self._ensure_prepay(public_id)
        return attempt

    def refresh_prepay(self, public_id: str) -> PaymentAttempt:
        self._require_mock_controls()
        return self._ensure_prepay(public_id)

    def close(self, public_id: str) -> PaymentAttempt:
        gateway = self._require_gateway()
        attempt = self._lock_attempt(public_id)
        self._assert_gateway_matches_attempt(attempt, gateway)
        current = PaymentStatus(attempt.status)
        if current == PaymentStatus.CLOSED:
            return attempt
        if current == PaymentStatus.SUCCEEDED:
            raise ApiError(
                409,
                "payment_already_succeeded",
                "A successful payment cannot be closed",
            )
        if current == PaymentStatus.FAILED:
            raise ApiError(409, "payment_failed", "A failed payment attempt cannot be closed")

        try:
            observed = gateway.query_payment(attempt.merchant_order_no)
        except PaymentGatewayError as exc:
            self.session.rollback()
            raise _gateway_api_error(exc) from exc
        self._apply_snapshot(attempt, observed, source="provider_query")
        if PaymentStatus(attempt.status) in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.CLOSED,
            PaymentStatus.FAILED,
        }:
            self.session.commit()
            return attempt

        try:
            gateway.close_payment(attempt.merchant_order_no)
            confirmed = gateway.query_payment(attempt.merchant_order_no)
        except PaymentGatewayError as exc:
            # WeChat close can lose a race with payment and return TRADE_ERROR.
            # Query once more before reporting failure so paid truth wins.
            try:
                recovered = gateway.query_payment(attempt.merchant_order_no)
            except PaymentGatewayError:
                self.session.rollback()
                raise _gateway_api_error(exc) from exc
            self._apply_snapshot(attempt, recovered, source="provider_query")
            if PaymentStatus(attempt.status) in {
                PaymentStatus.SUCCEEDED,
                PaymentStatus.CLOSED,
            }:
                if PaymentStatus(attempt.status) == PaymentStatus.CLOSED:
                    attempt.close_reason = "merchant_requested"
                self.session.commit()
                return attempt
            self.session.rollback()
            raise _gateway_api_error(exc) from exc
        self._apply_snapshot(
            attempt,
            confirmed,
            source="provider_query",
            reason_override="merchant_requested_close",
        )
        final_status = PaymentStatus(attempt.status)
        if final_status == PaymentStatus.CLOSED:
            attempt.close_reason = "merchant_requested"
        elif final_status != PaymentStatus.SUCCEEDED:
            self.session.rollback()
            raise ApiError(
                409,
                "payment_close_not_confirmed",
                "The provider did not confirm that the payment was closed",
            )
        self.session.commit()
        return attempt

    def process_notification(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
        *,
        _retry_after_integrity_error: bool = True,
    ) -> PaymentAttempt | None:
        gateway = self._require_gateway()
        try:
            notification = gateway.verify_and_decode_notification(raw_body, headers)
        except PaymentNotificationError as exc:
            raise ApiError(400, exc.code, exc.message) from exc

        payload_hash = hashlib.sha256(raw_body).hexdigest()
        existing = self.session.scalar(
            select(PaymentProviderEvent).where(
                PaymentProviderEvent.provider == gateway.provider_code,
                PaymentProviderEvent.provider_event_id == notification.provider_event_id,
            )
        )
        if existing is not None:
            if existing.payload_sha256 != payload_hash:
                raise ApiError(
                    409,
                    "provider_event_conflict",
                    "A provider event ID was reused with a different payload",
                )
            if existing.processing_status == "rejected":
                raise ApiError(
                    existing.error_status_code or 422,
                    existing.error_code or "provider_event_rejected",
                    existing.error_message or "Provider event was rejected",
                )
            return (
                self.session.get(PaymentAttempt, existing.payment_attempt_id)
                if existing.payment_attempt_id is not None
                else None
            )

        snapshot = notification.snapshot
        attempt = self._lock_attempt_by_merchant_order(
            provider=gateway.provider_code,
            provider_merchant_id=gateway.merchant_id,
            merchant_order_no=snapshot.merchant_order_no,
        )
        receipt = PaymentProviderEvent(
            provider=gateway.provider_code,
            provider_event_id=notification.provider_event_id,
            event_type=notification.event_type,
            provider_app_id=gateway.app_id,
            provider_merchant_id=gateway.merchant_id,
            merchant_order_no=snapshot.merchant_order_no,
            provider_state_raw=snapshot.trade_state.value,
            provider_transaction_id=snapshot.transaction_id,
            provider_success_time=snapshot.success_time,
            amount_cents=snapshot.amount_cents,
            currency=snapshot.currency,
            payment_attempt_id=attempt.id if attempt is not None else None,
            payload_sha256=payload_hash,
            signature_verified=True,
            processing_status="received",
            provider_created_at=notification.provider_created_at,
        )
        self.session.add(receipt)
        try:
            if attempt is None:
                receipt.processing_status = "ignored"
                receipt.error_code = "payment_not_found"
                receipt.error_message = "No local payment matched the merchant order number"
                receipt.processed_at = self._now()
                self.session.commit()
                return None

            try:
                self._assert_gateway_matches_attempt(attempt, gateway)
                decision = self._apply_snapshot(
                    attempt,
                    snapshot,
                    source="provider_notification",
                )
            except ApiError as exc:
                receipt.processing_status = "rejected"
                receipt.error_code = exc.code
                receipt.error_message = exc.message
                receipt.error_status_code = exc.status_code
                receipt.processed_at = self._now()
                self.session.commit()
                raise
            receipt.processing_status = (
                "ignored" if decision.action == TransitionAction.IGNORE else "processed"
            )
            receipt.processed_at = self._now()
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raced = self.session.scalar(
                select(PaymentProviderEvent).where(
                    PaymentProviderEvent.provider == gateway.provider_code,
                    PaymentProviderEvent.provider_event_id == notification.provider_event_id,
                )
            )
            if raced is not None and raced.payload_sha256 == payload_hash:
                if raced.processing_status == "rejected":
                    raise ApiError(
                        raced.error_status_code or 422,
                        raced.error_code or "provider_event_rejected",
                        raced.error_message or "Provider event was rejected",
                    ) from exc
                return (
                    self.session.get(PaymentAttempt, raced.payment_attempt_id)
                    if raced.payment_attempt_id is not None
                    else None
                )
            # A different uniqueness boundary (most importantly provider
            # transaction identity) may have resolved concurrently. Re-read
            # once so the now-visible conflict is classified and the verified
            # callback is durably retained as a rejected inbox record.
            if _retry_after_integrity_error:
                return self.process_notification(
                    raw_body,
                    headers,
                    _retry_after_integrity_error=False,
                )
            raise ApiError(
                409,
                "provider_event_conflict",
                "Provider event processing conflicted with another request",
            ) from exc
        return attempt

    def simulate_provider_state(
        self,
        public_id: str,
        *,
        trade_state: ProviderTradeState,
        deliver_callback: bool,
        provider_event_id: str | None,
    ) -> PaymentAttempt:
        gateway = self._require_mock_controls()
        attempt = self.get_attempt(public_id)
        try:
            gateway.set_trade_state(attempt.merchant_order_no, trade_state)
            if deliver_callback:
                raw_body, headers = gateway.build_success_notification(
                    attempt.merchant_order_no,
                    provider_event_id=provider_event_id,
                )
                self.session.rollback()
                processed = self.process_notification(raw_body, headers)
                assert processed is not None
                return processed
        except PaymentGatewayError as exc:
            raise _gateway_api_error(exc) from exc
        self.session.rollback()
        return self.get_attempt(public_id)

    def _ensure_prepay(self, public_id: str) -> PaymentAttempt:
        gateway = self._require_gateway()
        attempt = self._lock_attempt(public_id)
        self._assert_gateway_matches_attempt(attempt, gateway)
        current = PaymentStatus(attempt.status)
        if current == PaymentStatus.PENDING:
            if attempt.prepay_expires_at is not None and self._now() < _as_utc(
                attempt.prepay_expires_at
            ):
                return attempt
            try:
                observed = gateway.query_payment(attempt.merchant_order_no)
            except PaymentGatewayError as exc:
                raise _gateway_api_error(exc) from exc
            self._apply_snapshot(attempt, observed, source="provider_query")
            if PaymentStatus(attempt.status) != PaymentStatus.PENDING:
                self.session.commit()
                return attempt
        elif current != PaymentStatus.CREATED:
            return attempt
        previous_prepay_id = attempt.prepay_id
        try:
            result = gateway.create_prepay(
                PrepayRequest(
                    app_id=attempt.provider_app_id,
                    merchant_order_no=attempt.merchant_order_no,
                    description=attempt.description,
                    amount_cents=attempt.amount_cents,
                    currency=attempt.currency,
                    payer_openid=f"mock_openid_{attempt.owner_user_id}",
                    notify_url=("https://mock.invalid/api/v1/payments/providers/wechat-pay/notify"),
                    request_hash=attempt.request_hash,
                )
            )
        except PaymentGatewayError as exc:
            attempt.failure_code = exc.code
            attempt.failure_message = exc.message
            if not exc.retryable:
                self._apply_local_transition(
                    attempt,
                    PaymentStatus.FAILED,
                    event_type="payment.prepay_failed",
                    source="system",
                    reason_code=exc.code,
                )
            else:
                self._record_event(
                    attempt,
                    event_type="payment.prepay_retryable_error",
                    source="system",
                    from_status=PaymentStatus(attempt.status),
                    to_status=PaymentStatus(attempt.status),
                    reason_code=exc.code,
                    details={},
                )
            self.session.commit()
            raise _gateway_api_error(exc) from exc
        attempt.prepay_id = result.prepay_id
        attempt.prepay_expires_at = result.prepay_expires_at
        attempt.client_parameters = result.client_parameters
        attempt.provider_state_raw = result.provider_state.value
        attempt.failure_code = None
        attempt.failure_message = None
        if result.provider_state == ProviderTradeState.NOTPAY:
            if PaymentStatus(attempt.status) == PaymentStatus.CREATED:
                self._apply_local_transition(
                    attempt,
                    PaymentStatus.PENDING,
                    event_type="payment.prepay_created",
                    source="system",
                    reason_code="provider_prepay_created",
                )
            elif previous_prepay_id != result.prepay_id:
                self._record_event(
                    attempt,
                    event_type="payment.prepay_refreshed",
                    source="system",
                    from_status=PaymentStatus.PENDING,
                    to_status=PaymentStatus.PENDING,
                    reason_code="prepay_expired",
                    details={"previous_prepay_id": previous_prepay_id},
                )
        else:
            snapshot = gateway.query_payment(attempt.merchant_order_no)
            self._apply_snapshot(attempt, snapshot, source="provider_query")
        self.session.commit()
        return attempt

    def _apply_snapshot(
        self,
        attempt: PaymentAttempt,
        snapshot: PaymentSnapshot,
        *,
        source: str,
        reason_override: str | None = None,
    ) -> TransitionDecision:
        if snapshot.merchant_order_no != attempt.merchant_order_no:
            raise ApiError(422, "provider_order_mismatch", "Provider order number does not match")
        if snapshot.amount_cents != attempt.amount_cents or snapshot.currency != attempt.currency:
            raise ApiError(
                422,
                "provider_amount_mismatch",
                "Provider amount or currency does not match",
            )

        target = status_for_provider_state(snapshot.trade_state)
        current = PaymentStatus(attempt.status)
        if (
            current == PaymentStatus.CREATED
            and target == PaymentStatus.PENDING
            and attempt.prepay_id is None
        ):
            attempt.provider_state_raw = snapshot.trade_state.value
            self._record_event(
                attempt,
                event_type="payment.provider_state_observed",
                source=source,
                from_status=current,
                to_status=current,
                reason_code="prepay_not_recovered",
                details={"provider_state": snapshot.trade_state.value},
            )
            return TransitionDecision(TransitionAction.IGNORE, "prepay_not_recovered")
        decision = decide_transition(current, target, provider_authoritative=True)
        related_attempts: list[PaymentAttempt] = []
        review_reason: str | None = None
        if target == PaymentStatus.SUCCEEDED:
            if snapshot.transaction_id is None or snapshot.success_time is None:
                raise ApiError(
                    422,
                    "provider_success_incomplete",
                    "Successful provider state is missing transaction details",
                )
            if (
                attempt.provider_transaction_id is not None
                and attempt.provider_transaction_id != snapshot.transaction_id
            ):
                raise ApiError(
                    409,
                    "provider_transaction_mismatch",
                    "Successful payment transaction ID is immutable",
                )
            if attempt.paid_at is not None and not _same_instant(
                attempt.paid_at,
                snapshot.success_time,
            ):
                raise ApiError(
                    409,
                    "provider_success_time_mismatch",
                    "Successful payment timestamp is immutable",
                )
            duplicate_transaction = self.session.scalar(
                select(PaymentAttempt.id).where(
                    PaymentAttempt.provider == attempt.provider,
                    PaymentAttempt.provider_transaction_id == snapshot.transaction_id,
                    PaymentAttempt.id != attempt.id,
                )
            )
            if duplicate_transaction is not None:
                raise ApiError(
                    409,
                    "provider_transaction_conflict",
                    "Provider transaction ID is already attached to another payment",
                )
            related_attempts = [
                item
                for item in self._lock_order_attempts(attempt.order_reference)
                if item.id != attempt.id
                and item.status
                in {
                    PaymentStatus.CREATED.value,
                    PaymentStatus.PENDING.value,
                    PaymentStatus.SUCCEEDED.value,
                }
            ]
            if related_attempts:
                review_reason = (
                    "multiple_successful_attempts"
                    if any(
                        item.status == PaymentStatus.SUCCEEDED.value for item in related_attempts
                    )
                    else "success_with_active_attempt"
                )

        if decision.action == TransitionAction.APPLY:
            attempt.provider_state_raw = snapshot.trade_state.value
            if target == PaymentStatus.SUCCEEDED:
                attempt.provider_transaction_id = snapshot.transaction_id
                attempt.paid_at = snapshot.success_time
                attempt.closed_at = None
                attempt.failed_at = None
                attempt.close_reason = None
                attempt.failure_code = None
                attempt.failure_message = None
            elif target == PaymentStatus.CLOSED:
                attempt.closed_at = self._now()
            elif target == PaymentStatus.FAILED:
                attempt.failed_at = self._now()
                attempt.failure_code = snapshot.trade_state.value
            attempt.status = target.value
            reason = reason_override or decision.reason
            self._record_event(
                attempt,
                event_type=_event_type(target, decision),
                source=source,
                from_status=current,
                to_status=target,
                reason_code=reason,
                details={"provider_state": snapshot.trade_state.value},
            )
        elif decision.action == TransitionAction.NOOP:
            attempt.provider_state_raw = snapshot.trade_state.value
            if target == PaymentStatus.SUCCEEDED and attempt.provider_transaction_id is None:
                attempt.provider_transaction_id = snapshot.transaction_id
                attempt.paid_at = snapshot.success_time
        else:
            self._record_event(
                attempt,
                event_type="payment.provider_state_ignored",
                source=source,
                from_status=current,
                to_status=current,
                reason_code=decision.reason,
                details={"ignored_provider_state": snapshot.trade_state.value},
            )
        # Record the authoritative success transition before the anomaly it
        # revealed, so the append-only event history remains causally ordered.
        if review_reason is not None:
            self._mark_for_review(
                attempt,
                reason=review_reason,
                source=source,
                related_public_ids=[item.public_id for item in related_attempts],
            )
            for related in related_attempts:
                self._mark_for_review(
                    related,
                    reason=review_reason,
                    source=source,
                    related_public_ids=[attempt.public_id],
                )
        return decision

    def _mark_for_review(
        self,
        attempt: PaymentAttempt,
        *,
        reason: str,
        source: str,
        related_public_ids: list[str],
    ) -> None:
        if attempt.review_required and attempt.review_reason == reason:
            return
        attempt.review_required = True
        attempt.review_reason = reason
        current = PaymentStatus(attempt.status)
        self._record_event(
            attempt,
            event_type="payment.review_required",
            source=source,
            from_status=current,
            to_status=current,
            reason_code=reason,
            details={"related_payment_public_ids": related_public_ids},
        )

    def _apply_local_transition(
        self,
        attempt: PaymentAttempt,
        target: PaymentStatus,
        *,
        event_type: str,
        source: str,
        reason_code: str,
    ) -> None:
        current = PaymentStatus(attempt.status)
        decision = decide_transition(current, target, provider_authoritative=False)
        if decision.action != TransitionAction.APPLY:
            raise ApiError(409, "invalid_payment_transition", "Payment transition is not allowed")
        if target == PaymentStatus.FAILED:
            attempt.failed_at = self._now()
        attempt.status = target.value
        self._record_event(
            attempt,
            event_type=event_type,
            source=source,
            from_status=current,
            to_status=target,
            reason_code=reason_code,
            details={},
        )

    def _record_event(
        self,
        attempt: PaymentAttempt,
        *,
        event_type: str,
        source: str,
        from_status: PaymentStatus | None,
        to_status: PaymentStatus | None,
        reason_code: str | None,
        details: dict[str, object],
    ) -> None:
        self.session.add(
            PaymentStateEvent(
                event_id=str(uuid4()),
                payment_attempt=attempt,
                event_type=event_type,
                source=source,
                from_status=from_status.value if from_status is not None else None,
                to_status=to_status.value if to_status is not None else None,
                reason_code=reason_code,
                details=details,
            )
        )

    def _lock_attempt(self, public_id: str) -> PaymentAttempt:
        candidate = self.session.execute(
            select(PaymentAttempt.id, PaymentAttempt.order_reference).where(
                PaymentAttempt.public_id == public_id
            )
        ).one_or_none()
        if candidate is None:
            raise ApiError(404, "payment_not_found", "Payment attempt was not found")
        candidate_id, order_reference = candidate
        attempts = self._lock_order_attempts(order_reference)
        return next(item for item in attempts if item.id == candidate_id)

    def _lock_attempt_by_merchant_order(
        self,
        *,
        provider: str,
        provider_merchant_id: str,
        merchant_order_no: str,
    ) -> PaymentAttempt | None:
        candidate = self.session.execute(
            select(PaymentAttempt.id, PaymentAttempt.order_reference).where(
                PaymentAttempt.provider == provider,
                PaymentAttempt.provider_merchant_id == provider_merchant_id,
                PaymentAttempt.merchant_order_no == merchant_order_no,
            )
        ).one_or_none()
        if candidate is None:
            return None
        candidate_id, order_reference = candidate
        attempts = self._lock_order_attempts(order_reference)
        return next(item for item in attempts if item.id == candidate_id)

    def _lock_order_attempts(self, order_reference: str) -> list[PaymentAttempt]:
        # Always lock siblings in ascending ID order. Concurrent callbacks for
        # retry attempts cannot deadlock by acquiring A->B and B->A. Refresh
        # any identity-map entries after waiting for a concurrent transaction.
        return list(
            self.session.scalars(
                select(PaymentAttempt)
                .where(PaymentAttempt.order_reference == order_reference)
                .order_by(PaymentAttempt.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )

    @staticmethod
    def _assert_gateway_matches_attempt(
        attempt: PaymentAttempt,
        gateway: PaymentGateway,
    ) -> None:
        if (
            attempt.provider != gateway.provider_code
            or attempt.provider_mode != gateway.mode
            or attempt.provider_app_id != gateway.app_id
            or attempt.provider_merchant_id != gateway.merchant_id
        ):
            raise ApiError(
                409,
                "payment_gateway_mismatch",
                "Configured payment gateway does not match the stored payment identity",
            )

    @staticmethod
    def _assert_same_idempotent_request(attempt: PaymentAttempt, request_hash: str) -> None:
        if attempt.request_hash != request_hash:
            raise ApiError(
                409,
                "idempotency_key_reused",
                "X-Idempotency-Key was already used with a different request",
            )

    @staticmethod
    def _validate_new_attempt(
        owner_user_id: int,
        request: MockPaymentCreateRequest,
        prior_attempts: list[PaymentAttempt],
    ) -> None:
        for attempt in prior_attempts:
            if attempt.owner_user_id != owner_user_id:
                raise ApiError(
                    409,
                    "order_reference_conflict",
                    "Order reference belongs to a different user",
                )
            if attempt.amount_cents != request.amount_cents or attempt.currency != request.currency:
                raise ApiError(
                    409,
                    "order_amount_conflict",
                    "Payment attempts for an order must use the same stored amount",
                )
            status = PaymentStatus(attempt.status)
            if status == PaymentStatus.SUCCEEDED:
                raise ApiError(409, "order_already_paid", "This order is already paid")
            if status in {PaymentStatus.CREATED, PaymentStatus.PENDING}:
                raise ApiError(
                    409,
                    "payment_attempt_in_progress",
                    "An active payment attempt already exists for this order",
                )

    def _require_gateway(self) -> PaymentGateway:
        if self.gateway is None:
            raise ApiError(503, "payments_disabled", "Payments are not enabled")
        return self.gateway

    def _require_mock_controls(self) -> MockWeChatPayGateway:
        gateway = self._require_gateway()
        if not self.settings.payment_mock_controls_enabled or not isinstance(
            gateway, MockWeChatPayGateway
        ):
            raise ApiError(
                404,
                "mock_payments_unavailable",
                "Mock payment controls are unavailable",
            )
        return gateway


def _request_hash(request: MockPaymentCreateRequest, gateway: PaymentGateway) -> str:
    canonical = json.dumps(
        {
            "amount_cents": request.amount_cents,
            "currency": request.currency,
            "description": request.description,
            "order_reference": request.order_reference,
            "provider": gateway.provider_code,
            "provider_app_id": gateway.app_id,
            "provider_merchant_id": gateway.merchant_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _merchant_order_no(now: datetime) -> str:
    return f"HM{now.astimezone(UTC):%Y%m%d%H%M%S}{uuid4().hex[:16]}"


def _event_type(target: PaymentStatus, decision: TransitionDecision) -> str:
    if target == PaymentStatus.SUCCEEDED and decision.reason == "late_provider_success":
        return "payment.succeeded_late"
    return {
        PaymentStatus.PENDING: "payment.pending",
        PaymentStatus.SUCCEEDED: "payment.succeeded",
        PaymentStatus.CLOSED: "payment.closed",
        PaymentStatus.FAILED: "payment.failed",
        PaymentStatus.CREATED: "payment.created",
    }[target]


def _gateway_api_error(exc: PaymentGatewayError) -> ApiError:
    return ApiError(
        503 if exc.retryable else 502,
        f"payment_provider_{exc.code}",
        exc.message,
    )


def _same_instant(left: datetime, right: datetime) -> bool:
    return _as_utc(left) == _as_utc(right)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
