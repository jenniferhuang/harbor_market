from __future__ import annotations

import json
import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

ProductStatus = Literal["draft", "published", "archived"]
StockStatus = Literal["in_stock", "out_of_stock", "preorder"]
ImageType = Literal["cover", "gallery", "detail"]

POSTGRES_INTEGER_MIN = -(2**31)
POSTGRES_INTEGER_MAX = 2**31 - 1
MAX_PRODUCT_SPECIFICATIONS = 20
MAX_SPECIFICATION_OPTIONS = 50
MAX_SKU_ATTRIBUTES = 20
MAX_SPECIFICATIONS_JSON_CHARACTERS = 20_000
MAX_SKU_ATTRIBUTES_JSON_CHARACTERS = 2_000
MAX_SPECIFICATIONS_JSON_DEPTH = 4
MAX_SKU_ATTRIBUTES_JSON_DEPTH = 1

SpecificationCode = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("*", mode="before", check_fields=False)
    @classmethod
    def reject_xml_illegal_control_characters(cls, value: Any) -> Any:
        _reject_xml_illegal_control_characters_in_value(value)
        return value


def _reject_xml_illegal_control_characters(value: str) -> str:
    if any(ord(character) < 32 and character not in "\t\n\r" for character in value):
        raise ValueError("value contains an XML-illegal C0 control character")
    return value


def _reject_xml_illegal_control_characters_in_value(value: object) -> None:
    stack = [value]
    visited_containers: set[int] = set()
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            _reject_xml_illegal_control_characters(item)
            continue
        if isinstance(item, BaseModel):
            stack.append(item.model_dump(mode="python"))
            continue
        if isinstance(item, dict):
            container_id = id(item)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            stack.extend(item.keys())
            stack.extend(item.values())
            continue
        if isinstance(item, list | tuple | set):
            container_id = id(item)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            stack.extend(item)


def _validate_json_document(
    value: object,
    *,
    label: str,
    max_characters: int,
    max_depth: int,
) -> None:
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        if depth > max_depth:
            raise ValueError(f"{label} exceeds the maximum JSON nesting depth")
        if isinstance(item, str):
            _reject_xml_illegal_control_characters(item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{label} must not contain NaN or Infinity")
        elif isinstance(item, dict):
            for key, nested_value in item.items():
                if isinstance(key, str):
                    _reject_xml_illegal_control_characters(key)
                stack.append((nested_value, depth + 1))
        elif isinstance(item, list):
            stack.extend((nested_value, depth + 1) for nested_value in item)

    try:
        serialized = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be finite, JSON-serializable data") from exc
    if len(serialized) > max_characters:
        raise ValueError(f"{label} must serialize to at most {max_characters} characters")


class SpecificationOption(StrictModel):
    code: SpecificationCode
    name: str = Field(min_length=1, max_length=120)
    price_delta_cents: int = Field(
        default=0,
        strict=True,
        ge=POSTGRES_INTEGER_MIN,
        le=POSTGRES_INTEGER_MAX,
    )
    sort: int = Field(
        default=0,
        strict=True,
        ge=POSTGRES_INTEGER_MIN,
        le=POSTGRES_INTEGER_MAX,
    )
    is_default: bool = Field(default=False, strict=True)


class ProductSpecification(StrictModel):
    code: SpecificationCode
    name: str = Field(min_length=1, max_length=120)
    selection_mode: Literal["single", "multiple"] = "single"
    required: bool = Field(default=False, strict=True)
    min_select: int = Field(default=0, strict=True, ge=0, le=MAX_SPECIFICATION_OPTIONS)
    max_select: int = Field(default=1, strict=True, ge=1, le=MAX_SPECIFICATION_OPTIONS)
    options: list[SpecificationOption] = Field(
        default_factory=list,
        max_length=MAX_SPECIFICATION_OPTIONS,
    )

    @model_validator(mode="after")
    def validate_selection_contract(self) -> ProductSpecification:
        option_codes = [option.code for option in self.options]
        if len(option_codes) != len({code.casefold() for code in option_codes}):
            raise ValueError("specification option codes must be unique")
        if self.required and self.min_select == 0:
            raise ValueError("required specifications must have min_select of at least 1")
        if not self.required and self.min_select > 0:
            raise ValueError("optional specifications must have min_select of 0")
        if self.min_select > self.max_select:
            raise ValueError("min_select must be less than or equal to max_select")
        if self.selection_mode == "single" and self.max_select > 1:
            raise ValueError("single specifications must have max_select of at most 1")
        if self.min_select > len(self.options):
            raise ValueError("min_select must not exceed the number of options")
        if self.options and self.max_select > len(self.options):
            raise ValueError("max_select must not exceed the number of options")

        default_count = sum(option.is_default for option in self.options)
        if self.selection_mode == "single" and default_count > 1:
            raise ValueError("single specifications may have at most one default option")
        if default_count > self.max_select:
            raise ValueError("default option count must not exceed max_select")
        return self


def _validate_product_specifications(
    specifications: list[ProductSpecification],
) -> list[ProductSpecification]:
    codes = [specification.code for specification in specifications]
    if len(codes) != len({code.casefold() for code in codes}):
        raise ValueError("specification codes must be unique")
    _validate_json_document(
        [specification.model_dump(mode="json") for specification in specifications],
        label="specifications",
        max_characters=MAX_SPECIFICATIONS_JSON_CHARACTERS,
        max_depth=MAX_SPECIFICATIONS_JSON_DEPTH,
    )
    return specifications


class CategoryCreate(StrictModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=20_000)
    parent_id: int | None = Field(default=None, ge=1, le=POSTGRES_INTEGER_MAX)
    sort_order: int = Field(default=0, ge=POSTGRES_INTEGER_MIN, le=POSTGRES_INTEGER_MAX)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class CategoryUpdate(StrictModel):
    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=50,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=20_000)
    parent_id: int | None = Field(default=None, ge=1, le=POSTGRES_INTEGER_MAX)
    sort_order: int | None = Field(
        default=None,
        ge=POSTGRES_INTEGER_MIN,
        le=POSTGRES_INTEGER_MAX,
    )
    is_active: bool | None = None

    @field_validator("code", "name", "sort_order", "is_active", mode="before")
    @classmethod
    def reject_null_for_required_fields(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("field may not be null")
        return value

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class CategoryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    parent_id: int | None
    sort_order: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProductSkuInput(StrictModel):
    sku_code: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=160)
    price_cents: int = Field(ge=0, le=POSTGRES_INTEGER_MAX)
    market_price_cents: int | None = Field(default=None, ge=0, le=POSTGRES_INTEGER_MAX)
    stock_quantity: int = Field(default=0, ge=0, le=POSTGRES_INTEGER_MAX)
    attributes: dict[SpecificationCode, SpecificationCode] = Field(
        default_factory=dict,
        max_length=MAX_SKU_ATTRIBUTES,
    )
    is_default: bool = False
    is_active: bool = True
    sort_order: int = Field(default=0, ge=POSTGRES_INTEGER_MIN, le=POSTGRES_INTEGER_MAX)

    @field_validator("sku_code")
    @classmethod
    def normalize_sku_code(cls, value: str) -> str:
        return value.upper()

    @field_validator("attributes", mode="before")
    @classmethod
    def validate_attributes(cls, values: Any) -> Any:
        _validate_json_document(
            values,
            label="SKU attributes",
            max_characters=MAX_SKU_ATTRIBUTES_JSON_CHARACTERS,
            max_depth=MAX_SKU_ATTRIBUTES_JSON_DEPTH,
        )
        if isinstance(values, dict):
            string_keys = [key for key in values if isinstance(key, str)]
            if len(string_keys) != len({key.casefold() for key in string_keys}):
                raise ValueError("SKU attribute specification codes must be unique")
        return values

    @model_validator(mode="after")
    def validate_prices(self) -> ProductSkuInput:
        if self.market_price_cents is not None and self.market_price_cents < self.price_cents:
            raise ValueError("market_price_cents must be greater than or equal to price_cents")
        return self


def _validate_sku_attribute_references(
    specifications: list[ProductSpecification],
    skus: list[ProductSkuInput],
) -> None:
    attributed_skus = [sku for sku in skus if sku.attributes]
    if not attributed_skus:
        return

    # Legacy specification records only had code/name and therefore cannot validate references.
    if specifications and all(not specification.options for specification in specifications):
        return

    specifications_by_code = {specification.code: specification for specification in specifications}
    for sku in attributed_skus:
        for specification_code, option_code in sku.attributes.items():
            specification = specifications_by_code.get(specification_code)
            if specification is None:
                raise ValueError(
                    f"SKU {sku.sku_code} references unknown specification {specification_code}"
                )
            if specification.options and option_code not in {
                option.code for option in specification.options
            }:
                raise ValueError(
                    f"SKU {sku.sku_code} references unknown option "
                    f"{specification_code}.{option_code}"
                )


class ProductSkuRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sku_code: str
    name: str
    price_cents: int
    market_price_cents: int | None
    stock_quantity: int
    attributes: dict[str, str]
    is_default: bool
    is_active: bool
    sort_order: int
    created_at: datetime
    updated_at: datetime


class ProductFields(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    subtitle: str | None = Field(default=None, max_length=240)
    category_id: int = Field(ge=1, le=POSTGRES_INTEGER_MAX)
    status: ProductStatus = "draft"
    base_price_cents: int = Field(ge=0, le=POSTGRES_INTEGER_MAX)
    market_price_cents: int | None = Field(default=None, ge=0, le=POSTGRES_INTEGER_MAX)
    currency: Literal["CNY"] = "CNY"
    unit: str = Field(default="件", min_length=1, max_length=20)
    description: str = Field(default="", max_length=20_000)
    featured: bool = False
    stock_status: StockStatus = "in_stock"
    inventory_count: int | None = Field(default=None, ge=0, le=POSTGRES_INTEGER_MAX)
    tags: list[str] = Field(default_factory=list, max_length=20)
    selling_points: list[str] = Field(default_factory=list, max_length=5)
    specifications: list[ProductSpecification] = Field(
        default_factory=list,
        max_length=MAX_PRODUCT_SPECIFICATIONS,
    )
    ingredients: str | None = Field(default=None, max_length=5_000)
    allergen_info: str | None = Field(default=None, max_length=5_000)
    sort_order: int = Field(default=0, ge=POSTGRES_INTEGER_MIN, le=POSTGRES_INTEGER_MAX)

    @field_validator("tags", "selling_points")
    @classmethod
    def validate_text_lists(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("values must not contain duplicates")
        if any(len(value) > 100 for value in cleaned):
            raise ValueError("each value must be at most 100 characters")
        return cleaned

    @field_validator("specifications")
    @classmethod
    def validate_specifications(
        cls,
        values: list[ProductSpecification],
    ) -> list[ProductSpecification]:
        return _validate_product_specifications(values)

    @model_validator(mode="after")
    def validate_prices(self) -> ProductFields:
        if self.market_price_cents is not None and self.market_price_cents < self.base_price_cents:
            raise ValueError("market_price_cents must be greater than or equal to base_price_cents")
        return self


class ProductCreate(ProductFields):
    product_code: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    skus: list[ProductSkuInput] = Field(default_factory=list, max_length=100)

    @field_validator("product_code")
    @classmethod
    def normalize_product_code(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def validate_sku_attribute_references(self) -> ProductCreate:
        _validate_sku_attribute_references(self.specifications, self.skus)
        return self


class ProductUpdate(StrictModel):
    product_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$",
    )
    name: str | None = Field(default=None, min_length=1, max_length=160)
    subtitle: str | None = Field(default=None, max_length=240)
    category_id: int | None = Field(default=None, ge=1, le=POSTGRES_INTEGER_MAX)
    status: ProductStatus | None = None
    base_price_cents: int | None = Field(default=None, ge=0, le=POSTGRES_INTEGER_MAX)
    market_price_cents: int | None = Field(default=None, ge=0, le=POSTGRES_INTEGER_MAX)
    currency: Literal["CNY"] | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=20)
    description: str | None = Field(default=None, max_length=20_000)
    featured: bool | None = None
    stock_status: StockStatus | None = None
    inventory_count: int | None = Field(default=None, ge=0, le=POSTGRES_INTEGER_MAX)
    tags: list[str] | None = Field(default=None, max_length=20)
    selling_points: list[str] | None = Field(default=None, max_length=5)
    specifications: list[ProductSpecification] | None = Field(
        default=None,
        max_length=MAX_PRODUCT_SPECIFICATIONS,
    )
    ingredients: str | None = Field(default=None, max_length=5_000)
    allergen_info: str | None = Field(default=None, max_length=5_000)
    sort_order: int | None = Field(
        default=None,
        ge=POSTGRES_INTEGER_MIN,
        le=POSTGRES_INTEGER_MAX,
    )
    skus: list[ProductSkuInput] | None = Field(default=None, max_length=100)

    @field_validator(
        "product_code",
        "name",
        "category_id",
        "status",
        "base_price_cents",
        "currency",
        "unit",
        "description",
        "featured",
        "stock_status",
        "tags",
        "selling_points",
        "specifications",
        "sort_order",
        "skus",
        mode="before",
    )
    @classmethod
    def reject_null_for_required_fields(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("field may not be null")
        return value

    @field_validator("product_code")
    @classmethod
    def normalize_product_code(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("tags", "selling_points")
    @classmethod
    def validate_text_lists(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = [value.strip() for value in values if value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("values must not contain duplicates")
        if any(len(value) > 100 for value in cleaned):
            raise ValueError("each value must be at most 100 characters")
        return cleaned

    @field_validator("specifications")
    @classmethod
    def validate_specifications(
        cls,
        values: list[ProductSpecification] | None,
    ) -> list[ProductSpecification] | None:
        if values is None:
            return None
        return _validate_product_specifications(values)

    @model_validator(mode="after")
    def validate_sku_attribute_references(self) -> ProductUpdate:
        if self.specifications is not None and self.skus is not None:
            _validate_sku_attribute_references(self.specifications, self.skus)
        return self


class ProductImageRead(BaseModel):
    id: int
    object_key: str
    image_type: ImageType
    alt_text: str | None
    sort_order: int
    mime_type: str | None
    size_bytes: int | None
    width: int | None
    height: int | None
    url: str
    created_at: datetime


class ProductRead(BaseModel):
    id: int
    product_code: str
    name: str
    subtitle: str | None
    category: CategoryRead
    status: ProductStatus
    base_price_cents: int
    market_price_cents: int | None
    currency: str
    unit: str
    description: str
    featured: bool
    stock_status: StockStatus
    inventory_count: int | None
    tags: list[str]
    selling_points: list[str]
    specifications: list[ProductSpecification] = Field(max_length=MAX_PRODUCT_SPECIFICATIONS)
    ingredients: str | None
    allergen_info: str | None
    sort_order: int
    skus: list[ProductSkuRead]
    images: list[ProductImageRead]
    created_at: datetime
    updated_at: datetime

    @field_validator("specifications")
    @classmethod
    def validate_specifications(
        cls,
        values: list[ProductSpecification],
    ) -> list[ProductSpecification]:
        return _validate_product_specifications(values)


class ImageUpdate(StrictModel):
    alt_text: str | None = Field(default=None, max_length=200)
    sort_order: int | None = Field(
        default=None,
        ge=POSTGRES_INTEGER_MIN,
        le=POSTGRES_INTEGER_MAX,
    )

    @field_validator("sort_order", mode="before")
    @classmethod
    def reject_null_for_required_fields(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("field may not be null")
        return value


class ProductResponse(BaseModel):
    data: ProductRead


class ProductListData(BaseModel):
    items: list[ProductRead]
    total: int
    page: int
    page_size: int


class ProductListResponse(BaseModel):
    data: ProductListData


class CategoryListResponse(BaseModel):
    data: list[CategoryRead]


class CategoryResponse(BaseModel):
    data: CategoryRead


class ImportErrorItem(BaseModel):
    sheet: str
    row: int
    field: str
    message: str


class ImportResultData(BaseModel):
    job_id: int
    dry_run: bool
    valid: bool
    summary: dict[str, int]
    errors: list[ImportErrorItem]


class ImportResultResponse(BaseModel):
    data: ImportResultData


class ImportJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_by: int
    status: str
    original_filename: str
    workbook_sha256: str
    idempotency_key: str | None
    dry_run: bool
    summary: dict[str, Any]
    errors: list[dict[str, Any]]
    created_at: datetime
    completed_at: datetime | None


class ImportJobResponse(BaseModel):
    data: ImportJobRead


class ImportJobListResponse(BaseModel):
    data: list[ImportJobRead]
