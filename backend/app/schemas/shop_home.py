from __future__ import annotations

import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.catalog import ProductRead
from app.schemas.shop_store import StoreRead


class ShopSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    q: str = Field(min_length=1, max_length=160)
    page: int = Field(default=1, ge=1, le=1_000_000)
    page_size: int = Field(default=10, ge=1, le=100)

    @field_validator("q")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("请输入搜索内容")
        if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
            raise ValueError("搜索内容含有不支持的字符")
        return value


class HomeCarousel(BaseModel):
    id: int
    title: str
    media_type: Literal["image", "video"]
    url: str


class HomeCategory(BaseModel):
    id: int
    code: str
    name: str
    image_url: str | None


class HomeCoupon(BaseModel):
    id: int
    title: str
    min_spend_cents: int
    discount_cents: int
    starts_at: datetime
    expires_at: datetime


class HotProduct(BaseModel):
    product: ProductRead
    sold_quantity: int
    order_count: int
    repeat_purchase_count: int


class HotSearch(BaseModel):
    product: ProductRead
    search_hit_count: int


class HotSearchResponse(BaseModel):
    data: list[HotSearch]


class ShopHomeData(BaseModel):
    store: StoreRead
    carousel: list[HomeCarousel]
    coupons: list[HomeCoupon]
    categories: list[HomeCategory]
    hot_products: list[HotProduct]
    hot_products_source: Literal["sales", "featured", "newest"]


class ShopHomeResponse(BaseModel):
    data: ShopHomeData


class ShopCustomerRead(BaseModel):
    id: int
    nickname: str


class ShopCustomerListResponse(BaseModel):
    data: list[ShopCustomerRead]
