from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.catalog import ProductRead

MAX_AMOUNT_CENTS = 2**31 - 1
Money = Annotated[int, Field(strict=True, ge=0, le=MAX_AMOUNT_CENTS)]
PositiveMoney = Annotated[int, Field(strict=True, ge=1, le=MAX_AMOUNT_CENTS)]


class CommerceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_control_characters(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(character) < 32 for character in value):
            raise ValueError("参数包含无效控制字符")
        return value


class EmptyRequest(CommerceRequest):
    pass


class CouponCreate(CommerceRequest):
    title: str = Field(min_length=1, max_length=160)
    min_spend_cents: PositiveMoney
    discount_cents: PositiveMoney
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    is_active: bool = Field(default=True, strict=True)

    @field_validator("starts_at", "expires_at")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_definition(self) -> CouponCreate:
        if self.discount_cents > self.min_spend_cents:
            raise ValueError("优惠金额不能超过满减门槛")
        if self.starts_at >= self.expires_at:
            raise ValueError("优惠券结束时间必须晚于开始时间")
        return self


class CouponUpdate(CommerceRequest):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    min_spend_cents: PositiveMoney | None = None
    discount_cents: PositiveMoney | None = None
    starts_at: AwareDatetime | None = None
    expires_at: AwareDatetime | None = None
    is_active: bool | None = Field(default=None, strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("参数不能为空")
        return value

    @field_validator("starts_at", "expires_at")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class CouponRead(BaseModel):
    coupon_id: int
    title: str
    min_spend_cents: int
    discount_cents: int
    starts_at: datetime
    expires_at: datetime
    is_active: bool
    claimed_at: datetime | None


class AdminCouponRead(BaseModel):
    id: int
    title: str
    min_spend_cents: int
    discount_cents: int
    starts_at: datetime
    expires_at: datetime
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CouponResponse(BaseModel):
    data: CouponRead


class CouponListResponse(BaseModel):
    data: list[CouponRead]


class AdminCouponResponse(BaseModel):
    data: AdminCouponRead


class AdminCouponListResponse(BaseModel):
    data: list[AdminCouponRead]


class FavoriteState(BaseModel):
    is_favorite: bool


class FavoriteResponse(BaseModel):
    data: FavoriteState


class FavoriteListResponse(BaseModel):
    data: list[ProductRead]


class HistoricalOrderItemCreate(CommerceRequest):
    product_code: str = Field(min_length=1, max_length=64)
    quantity: int = Field(strict=True, ge=1, le=100_000)
    unit_price_cents: Money

    @field_validator("product_code")
    @classmethod
    def normalize_product_code(cls, value: str) -> str:
        normalized = value.upper()
        if len(normalized) > 64:
            raise ValueError("商品编号超过允许长度")
        return normalized


class HistoricalOrderCreate(CommerceRequest):
    external_reference: str = Field(min_length=1, max_length=100)
    customer_id: Annotated[int, Field(strict=True, ge=1, le=MAX_AMOUNT_CENTS)] | None = None
    completed_at: AwareDatetime | None = None
    items: list[HistoricalOrderItemCreate] = Field(min_length=1, max_length=100)

    @field_validator("completed_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None


class OrderItemRead(BaseModel):
    product_code: str
    product_name: str
    unit_price_cents: int
    quantity: int
    line_total_cents: int


class OrderRead(BaseModel):
    id: int
    external_reference: str
    customer_id: int | None
    status: Literal["completed", "voided"]
    total_cents: int
    completed_at: datetime
    items: list[OrderItemRead]


class OrderResponse(BaseModel):
    data: OrderRead


class OrderListResponse(BaseModel):
    data: list[OrderRead]
