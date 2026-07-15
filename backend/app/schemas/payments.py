from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class MockPaymentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_reference: str = Field(
        min_length=6,
        max_length=64,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    amount_cents: int = Field(ge=1, le=100_000_000)
    currency: Literal["CNY"] = "CNY"
    description: str = Field(min_length=1, max_length=127)

    @field_validator("order_reference", "description", mode="before")
    @classmethod
    def strip_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class MockProviderStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_state: Literal["NOTPAY", "SUCCESS", "CLOSED"]
    deliver_callback: bool = False
    provider_event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )

    @model_validator(mode="after")
    def callback_only_for_success(self) -> MockProviderStateRequest:
        if self.deliver_callback and self.trade_state != "SUCCESS":
            raise ValueError("Only SUCCESS emits a WeChat Pay transaction callback")
        return self


class PaymentAttemptPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    public_id: str
    owner_user_id: int
    order_reference: str
    merchant_order_no: str
    provider: str
    provider_mode: str
    status: Literal["created", "pending", "succeeded", "closed", "failed"]
    provider_state_raw: str | None
    amount_cents: int
    currency: str
    description: str
    prepay_expires_at: datetime | None
    client_parameters: dict[str, str | bool] | None
    provider_transaction_id: str | None
    failure_code: str | None
    close_reason: str | None
    review_required: bool
    review_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None
    closed_at: datetime | None
    failed_at: datetime | None


class PaymentStateEventPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_id: str
    event_type: str
    source: str
    from_status: str | None
    to_status: str | None
    reason_code: str | None
    details: dict[str, object]
    created_at: datetime


class PaymentAttemptDetail(PaymentAttemptPublic):
    events: list[PaymentStateEventPublic]


class PaymentAttemptResponse(BaseModel):
    data: PaymentAttemptPublic


class PaymentAttemptDetailResponse(BaseModel):
    data: PaymentAttemptDetail
