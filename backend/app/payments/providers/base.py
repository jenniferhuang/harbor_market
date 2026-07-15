from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.payments.domain import ProviderTradeState


class PaymentGatewayError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class PaymentNotificationError(PaymentGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class PrepayRequest:
    app_id: str
    merchant_order_no: str
    description: str
    amount_cents: int
    currency: str
    payer_openid: str
    notify_url: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class PrepayResult:
    prepay_id: str
    prepay_expires_at: datetime
    client_parameters: dict[str, str | bool]
    provider_state: ProviderTradeState


@dataclass(frozen=True, slots=True)
class PaymentSnapshot:
    merchant_order_no: str
    trade_state: ProviderTradeState
    amount_cents: int
    currency: str
    transaction_id: str | None = None
    success_time: datetime | None = None


@dataclass(frozen=True, slots=True)
class PaymentNotification:
    provider_event_id: str
    event_type: str
    provider_created_at: datetime
    snapshot: PaymentSnapshot


class PaymentGateway(Protocol):
    provider_code: str
    mode: str
    app_id: str
    merchant_id: str

    def create_prepay(self, request: PrepayRequest) -> PrepayResult: ...

    def query_payment(self, merchant_order_no: str) -> PaymentSnapshot: ...

    def close_payment(self, merchant_order_no: str) -> None: ...

    def verify_and_decode_notification(
        self,
        raw_body: bytes,
        headers: Mapping[str, str],
    ) -> PaymentNotification: ...
