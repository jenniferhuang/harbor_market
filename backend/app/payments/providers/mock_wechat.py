from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import MockPaymentProviderRecord
from app.payments.domain import ProviderTradeState
from app.payments.providers.base import (
    PaymentGatewayError,
    PaymentNotification,
    PaymentNotificationError,
    PaymentSnapshot,
    PrepayRequest,
    PrepayResult,
)


class MockWeChatPayGateway:
    """Durable development double for the WeChat Pay v3 JSAPI boundary.

    Provider state lives in its own DB table so application restarts and multiple
    workers do not strand pending mock payments. The cryptography remains
    deliberately HMAC-based and is marked as unusable by ``wx.requestPayment``.
    """

    provider_code = "wechat_pay"
    mode = "mock"
    merchant_id = "mock-merchant"

    def __init__(
        self,
        *,
        app_id: str,
        signing_secret: str,
        session_factory: sessionmaker[Session],
        prepay_ttl_seconds: int = 2 * 60 * 60,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.app_id = app_id
        self._secret = signing_secret.encode("utf-8")
        self._session_factory = session_factory
        self._prepay_ttl_seconds = prepay_ttl_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def create_prepay(self, request: PrepayRequest) -> PrepayResult:
        self._validate_prepay_request(request)
        fingerprint = _prepay_fingerprint(request)
        with self._session_factory() as session:
            record = session.scalar(
                select(MockPaymentProviderRecord)
                .where(MockPaymentProviderRecord.merchant_order_no == request.merchant_order_no)
                .with_for_update()
            )
            if record is not None:
                if record.request_fingerprint != fingerprint:
                    raise PaymentGatewayError(
                        "duplicate_order_mismatch",
                        "The merchant order number was reused with different parameters",
                    )
                if record.trade_state == ProviderTradeState.NOTPAY.value and self._now() >= _as_utc(
                    record.prepay_expires_at
                ):
                    self._refresh_prepay(record, request)
                    session.commit()
                return _prepay_result(record)

            result = self._new_prepay_result(request, generation=1)
            record = MockPaymentProviderRecord(
                merchant_order_no=request.merchant_order_no,
                request_fingerprint=fingerprint,
                app_id=request.app_id,
                amount_cents=request.amount_cents,
                currency=request.currency,
                prepay_id=result.prepay_id,
                prepay_generation=1,
                prepay_expires_at=result.prepay_expires_at,
                client_parameters=result.client_parameters,
                trade_state=ProviderTradeState.NOTPAY.value,
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raced = session.get(MockPaymentProviderRecord, request.merchant_order_no)
                if raced is None or raced.request_fingerprint != fingerprint:
                    raise PaymentGatewayError(
                        "duplicate_order_mismatch",
                        "The merchant order number was reused with different parameters",
                    ) from exc
                return _prepay_result(raced)
            return result

    def query_payment(self, merchant_order_no: str) -> PaymentSnapshot:
        with self._session_factory() as session:
            record = self._get_record(session, merchant_order_no)
            return _snapshot(record)

    def close_payment(self, merchant_order_no: str) -> None:
        with self._session_factory() as session:
            record = self._get_record(session, merchant_order_no, for_update=True)
            if record.trade_state not in {
                ProviderTradeState.SUCCESS.value,
                ProviderTradeState.REFUND.value,
            }:
                record.trade_state = ProviderTradeState.CLOSED.value
                session.commit()

    def set_trade_state(
        self,
        merchant_order_no: str,
        trade_state: ProviderTradeState,
    ) -> PaymentSnapshot:
        """Development control used only by guarded admin mock endpoints."""

        with self._session_factory() as session:
            record = self._get_record(session, merchant_order_no, for_update=True)
            record.trade_state = trade_state.value
            if trade_state in {ProviderTradeState.SUCCESS, ProviderTradeState.REFUND}:
                if record.transaction_id is None:
                    digest = hashlib.sha256(merchant_order_no.encode()).hexdigest()
                    record.transaction_id = f"mock_tx_{digest[:32]}"
                record.success_time = record.success_time or self._now()
            session.commit()
            return _snapshot(record)

    def build_success_notification(
        self,
        merchant_order_no: str,
        *,
        provider_event_id: str | None = None,
        amount_cents: int | None = None,
        currency: str | None = None,
        out_trade_no: str | None = None,
        transaction_id: str | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        snapshot = self.query_payment(merchant_order_no)
        if snapshot.trade_state not in {
            ProviderTradeState.SUCCESS,
            ProviderTradeState.REFUND,
        }:
            raise PaymentGatewayError(
                "payment_not_successful",
                "Only successful mock payments emit transaction callbacks",
            )

        created_at = self._now()
        payload = {
            "id": provider_event_id or f"mock_evt_{uuid4().hex}",
            "create_time": _format_datetime(created_at),
            "event_type": "TRANSACTION.SUCCESS",
            "resource_type": "mock-plain-resource",
            "resource": {
                "appid": self.app_id,
                "mchid": self.merchant_id,
                "out_trade_no": out_trade_no or snapshot.merchant_order_no,
                "transaction_id": transaction_id or snapshot.transaction_id,
                "trade_state": "SUCCESS",
                "success_time": _format_datetime(snapshot.success_time or created_at),
                "amount": {
                    "total": (amount_cents if amount_cents is not None else snapshot.amount_cents),
                    "currency": currency or snapshot.currency,
                },
            },
        }
        raw_body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        timestamp = str(int(created_at.timestamp()))
        nonce = secrets.token_hex(16)
        signature = self._notification_signature(timestamp, nonce, raw_body)
        return raw_body, {
            "Wechatpay-Timestamp": timestamp,
            "Wechatpay-Nonce": nonce,
            "Wechatpay-Signature": signature,
            "Wechatpay-Serial": "MOCK",
        }

    def verify_and_decode_notification(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> PaymentNotification:
        normalized_headers = {key.casefold(): value for key, value in headers.items()}
        try:
            timestamp = normalized_headers["wechatpay-timestamp"]
            nonce = normalized_headers["wechatpay-nonce"]
            signature = normalized_headers["wechatpay-signature"]
        except KeyError as exc:
            raise PaymentNotificationError(
                "notification_headers_missing",
                "Required WeChat Pay notification headers are missing",
            ) from exc
        try:
            signed_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
        except (ValueError, OverflowError) as exc:
            raise PaymentNotificationError(
                "notification_timestamp_invalid",
                "Notification timestamp is invalid",
            ) from exc
        if abs((self._now() - signed_at).total_seconds()) > 5 * 60:
            raise PaymentNotificationError(
                "notification_expired",
                "Notification timestamp is outside the five-minute replay window",
            )
        expected = self._notification_signature(timestamp, nonce, raw_body)
        if not hmac.compare_digest(signature, expected):
            raise PaymentNotificationError(
                "notification_signature_invalid",
                "Notification signature is invalid",
            )

        try:
            payload = json.loads(raw_body)
            resource = payload["resource"]
            amount = resource["amount"]
            event_id = _required_string(payload["id"], "id", maximum=128)
            event_type = _required_string(payload["event_type"], "event_type", maximum=64)
            merchant_order_no = _required_string(
                resource["out_trade_no"],
                "out_trade_no",
                maximum=32,
            )
            transaction_id = _required_string(
                resource["transaction_id"],
                "transaction_id",
                maximum=64,
            )
            trade_state = ProviderTradeState(resource["trade_state"])
            total = int(amount["total"])
            currency = _required_string(amount["currency"], "currency", maximum=3)
            provider_created_at = _parse_datetime(payload["create_time"])
            success_time = _parse_datetime(resource["success_time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PaymentNotificationError(
                "notification_payload_invalid",
                "Notification payload is invalid",
            ) from exc
        if event_type != "TRANSACTION.SUCCESS" or trade_state != ProviderTradeState.SUCCESS:
            raise PaymentNotificationError(
                "notification_event_unsupported",
                "Only successful transaction notifications are supported",
            )
        if resource.get("appid") != self.app_id or resource.get("mchid") != self.merchant_id:
            raise PaymentNotificationError(
                "notification_merchant_mismatch",
                "Notification AppID or merchant ID does not match",
            )
        return PaymentNotification(
            provider_event_id=event_id,
            event_type=event_type,
            provider_created_at=provider_created_at,
            snapshot=PaymentSnapshot(
                merchant_order_no=merchant_order_no,
                trade_state=trade_state,
                amount_cents=total,
                currency=currency,
                transaction_id=transaction_id,
                success_time=success_time,
            ),
        )

    def _validate_prepay_request(self, request: PrepayRequest) -> None:
        if request.app_id != self.app_id:
            raise PaymentGatewayError("appid_mismatch", "Mock AppID does not match")
        if request.amount_cents <= 0 or request.currency != "CNY":
            raise PaymentGatewayError("invalid_amount", "Mock payments require positive CNY fen")
        if not request.payer_openid.startswith("mock_openid_"):
            raise PaymentGatewayError("invalid_payer", "Mock payer identity is invalid")
        if not request.notify_url.startswith("https://"):
            raise PaymentGatewayError("invalid_notify_url", "Payment notify URL must use HTTPS")

    def _get_record(
        self,
        session: Session,
        merchant_order_no: str,
        *,
        for_update: bool = False,
    ) -> MockPaymentProviderRecord:
        statement = select(MockPaymentProviderRecord).where(
            MockPaymentProviderRecord.merchant_order_no == merchant_order_no
        )
        if for_update:
            statement = statement.with_for_update()
        record = session.scalar(statement)
        if record is None:
            raise PaymentGatewayError(
                "payment_not_found",
                "The mock provider does not know this merchant order number",
            )
        return record

    def _refresh_prepay(
        self,
        record: MockPaymentProviderRecord,
        request: PrepayRequest,
    ) -> None:
        generation = record.prepay_generation + 1
        result = self._new_prepay_result(request, generation=generation)
        record.prepay_generation = generation
        record.prepay_id = result.prepay_id
        record.prepay_expires_at = result.prepay_expires_at
        record.client_parameters = result.client_parameters

    def _new_prepay_result(self, request: PrepayRequest, *, generation: int) -> PrepayResult:
        digest = hashlib.sha256(f"{request.merchant_order_no}:{generation}".encode()).hexdigest()
        prepay_id = f"mock_prepay_{digest[:40]}"
        timestamp = str(int(self._now().timestamp()))
        nonce = secrets.token_hex(16)
        package = f"prepay_id={prepay_id}"
        signing_message = f"{self.app_id}\n{timestamp}\n{nonce}\n{package}\n".encode()
        pay_sign = hmac.new(self._secret, signing_message, hashlib.sha256).hexdigest()
        return PrepayResult(
            prepay_id=prepay_id,
            prepay_expires_at=self._now() + timedelta(seconds=self._prepay_ttl_seconds),
            client_parameters={
                "appId": self.app_id,
                "timeStamp": timestamp,
                "nonceStr": nonce,
                "package": package,
                "signType": "MOCK-HMAC-SHA256",
                "paySign": pay_sign,
                "mock": True,
            },
            provider_state=ProviderTradeState.NOTPAY,
        )

    def _notification_signature(self, timestamp: str, nonce: str, raw_body: bytes) -> str:
        message = timestamp.encode() + b"\n" + nonce.encode() + b"\n" + raw_body + b"\n"
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()


def _prepay_fingerprint(request: PrepayRequest) -> str:
    payload = json.dumps(
        {
            "app_id": request.app_id,
            "merchant_order_no": request.merchant_order_no,
            "description": request.description,
            "amount_cents": request.amount_cents,
            "currency": request.currency,
            "payer_openid": request.payer_openid,
            "notify_url": request.notify_url,
            "request_hash": request.request_hash,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _prepay_result(record: MockPaymentProviderRecord) -> PrepayResult:
    return PrepayResult(
        prepay_id=record.prepay_id,
        prepay_expires_at=_as_utc(record.prepay_expires_at),
        client_parameters=dict(record.client_parameters),
        provider_state=ProviderTradeState(record.trade_state),
    )


def _snapshot(record: MockPaymentProviderRecord) -> PaymentSnapshot:
    return PaymentSnapshot(
        merchant_order_no=record.merchant_order_no,
        trade_state=ProviderTradeState(record.trade_state),
        amount_cents=record.amount_cents,
        currency=record.currency,
        transaction_id=record.transaction_id,
        success_time=_as_utc(record.success_time) if record.success_time is not None else None,
    )


def _required_string(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError(f"{field} is invalid")
    return value


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("datetime is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("datetime must contain a timezone")
    return parsed.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
