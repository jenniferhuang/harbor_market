from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ShopMediaKind = Literal["carousel", "category", "announcement"]
ShopMediaType = Literal["image", "video"]
_INTEGER_MAX = 2**31 - 1
_INTEGER_MIN = -(2**31)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def reject_control_characters(cls, value: Any) -> Any:
        if isinstance(value, str) and any(
            ord(character) < 32 and character not in "\t\n\r" for character in value
        ):
            raise ValueError("内容含有不支持的控制字符")
        return value


class StorePatch(_StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @field_validator("name", "phone", "address", mode="before")
    @classmethod
    def reject_null_text(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("此字段不能设为空值")
        return value

    @field_validator("latitude", "longitude")
    @classmethod
    def finite_coordinate(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("门店坐标必须为有效数值")
        return value


class AdminStorePatch(StorePatch):
    owner_customer_id: int | None = Field(default=None, strict=True, ge=1, le=_INTEGER_MAX)


class StoreRead(BaseModel):
    name: str
    phone: str
    address: str
    latitude: float | None
    longitude: float | None
    announcement_image_url: str | None


class AdminStoreRead(StoreRead):
    owner_customer_id: int | None


class StoreResponse(BaseModel):
    data: StoreRead


class AdminStoreResponse(BaseModel):
    data: AdminStoreRead


class MiniStoreMeData(BaseModel):
    can_manage_store: bool


class MiniStoreMeResponse(BaseModel):
    data: MiniStoreMeData


class ShopMediaCreate(_StrictModel):
    kind: ShopMediaKind
    category_id: int | None = Field(default=None, ge=1, le=_INTEGER_MAX)
    title: str = Field(default="", max_length=160)
    sort_order: int = Field(default=0, ge=_INTEGER_MIN, le=_INTEGER_MAX)
    is_active: bool = True

    @model_validator(mode="after")
    def category_matches_kind(self) -> ShopMediaCreate:
        if (self.kind == "category") != (self.category_id is not None):
            raise ValueError("分类图片必须指定分类，其他素材不能指定分类")
        return self


class ShopMediaPatch(_StrictModel):
    title: str | None = Field(default=None, max_length=160)
    sort_order: int | None = Field(default=None, strict=True, ge=_INTEGER_MIN, le=_INTEGER_MAX)
    is_active: bool | None = Field(default=None, strict=True)

    @field_validator("title", "sort_order", "is_active", mode="before")
    @classmethod
    def reject_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("此字段不能设为空值")
        return value


class ShopMediaRead(BaseModel):
    id: int
    kind: ShopMediaKind
    category_id: int | None
    title: str
    media_type: ShopMediaType
    url: str
    sort_order: int
    is_active: bool


class ShopMediaResponse(BaseModel):
    data: ShopMediaRead


class ShopMediaListResponse(BaseModel):
    data: list[ShopMediaRead]


class ShopMediaDeletedData(BaseModel):
    deleted: Literal[True] = True


class ShopMediaDeletedResponse(BaseModel):
    data: ShopMediaDeletedData
