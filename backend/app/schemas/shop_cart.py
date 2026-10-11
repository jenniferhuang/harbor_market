from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.schemas.catalog import SpecificationCode
from app.schemas.shop_commerce import MAX_AMOUNT_CENTS, CommerceRequest

ProductCode = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=64)]
SkuCode = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=80)]
SelectedOptions = Annotated[list[SpecificationCode], Field(max_length=50)]
Selections = Annotated[dict[SpecificationCode, SelectedOptions], Field(max_length=20)]


class CartQuoteItemRequest(CommerceRequest):
    product_code: ProductCode
    sku_code: SkuCode
    selections: Selections = Field(default_factory=dict)
    quantity: int = Field(strict=True, ge=1, le=99)

    @field_validator("selections")
    @classmethod
    def reject_duplicate_options(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if any(len(options) != len(set(options)) for options in value.values()):
            raise ValueError("规格选项不能重复")
        return value


class CartQuoteRequest(CommerceRequest):
    items: list[CartQuoteItemRequest] = Field(max_length=100)
    coupon_id: Annotated[int, Field(strict=True, ge=1, le=MAX_AMOUNT_CENTS)] | None = None


class CartQuoteItemRead(BaseModel):
    product_code: str
    sku_code: str
    name: str
    sku_name: str
    selections: dict[str, list[str]]
    quantity: int
    unit_price_cents: int
    line_total_cents: int


class CartCouponEvaluation(BaseModel):
    coupon_id: int
    title: str
    min_spend_cents: int
    discount_cents: int
    eligible: bool
    shortfall_cents: int
    reason: Literal["eligible", "below_minimum", "inactive", "not_started", "expired"]


class CartQuoteData(BaseModel):
    items: list[CartQuoteItemRead]
    subtotal_cents: int
    discount_cents: int
    total_cents: int
    coupon_id: int | None
    coupon_evaluations: list[CartCouponEvaluation]


class CartQuoteResponse(BaseModel):
    data: CartQuoteData
