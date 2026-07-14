from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.db.session import build_engine
from app.schemas.catalog import (
    POSTGRES_INTEGER_MAX,
    POSTGRES_INTEGER_MIN,
    CategoryCreate,
    CategoryUpdate,
    ImageUpdate,
    ProductCreate,
    ProductSkuInput,
    ProductUpdate,
)


@pytest.mark.parametrize(
    ("model_type", "field_name"),
    [
        (CategoryUpdate, "code"),
        (CategoryUpdate, "name"),
        (CategoryUpdate, "sort_order"),
        (CategoryUpdate, "is_active"),
        (ProductUpdate, "product_code"),
        (ProductUpdate, "name"),
        (ProductUpdate, "category_id"),
        (ProductUpdate, "status"),
        (ProductUpdate, "base_price_cents"),
        (ProductUpdate, "currency"),
        (ProductUpdate, "unit"),
        (ProductUpdate, "description"),
        (ProductUpdate, "featured"),
        (ProductUpdate, "stock_status"),
        (ProductUpdate, "tags"),
        (ProductUpdate, "selling_points"),
        (ProductUpdate, "specifications"),
        (ProductUpdate, "sort_order"),
        (ProductUpdate, "skus"),
        (ImageUpdate, "sort_order"),
    ],
)
def test_patch_schemas_reject_explicit_null_for_non_nullable_fields(
    model_type: type[BaseModel],
    field_name: str,
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        model_type.model_validate({field_name: None})

    assert exc_info.value.errors()[0]["loc"] == (field_name,)
    assert exc_info.value.errors()[0]["type"] == "value_error"


@pytest.mark.parametrize(
    ("model_type", "payload"),
    [
        (CategoryUpdate, {"description": None, "parent_id": None}),
        (
            ProductUpdate,
            {
                "subtitle": None,
                "market_price_cents": None,
                "inventory_count": None,
                "ingredients": None,
                "allergen_info": None,
            },
        ),
        (ImageUpdate, {"alt_text": None}),
    ],
)
def test_patch_schemas_allow_explicit_null_only_for_nullable_fields(
    model_type: type[BaseModel],
    payload: dict[str, None],
) -> None:
    validated = model_type.model_validate(payload)

    assert validated.model_dump(exclude_unset=True) == payload


@pytest.mark.parametrize("model_type", [CategoryUpdate, ProductUpdate, ImageUpdate])
def test_patch_schemas_still_allow_omitted_fields(model_type: type[BaseModel]) -> None:
    assert model_type.model_validate({}).model_dump(exclude_unset=True) == {}


@pytest.mark.parametrize(
    ("model_type", "base_payload", "field_name", "minimum"),
    [
        (CategoryCreate, {"code": "COFFEE", "name": "Coffee"}, "parent_id", 1),
        (CategoryCreate, {"code": "COFFEE", "name": "Coffee"}, "sort_order", POSTGRES_INTEGER_MIN),
        (CategoryUpdate, {}, "parent_id", 1),
        (CategoryUpdate, {}, "sort_order", POSTGRES_INTEGER_MIN),
        (
            ProductSkuInput,
            {"sku_code": "SKU-1", "name": "Default", "price_cents": 0},
            "price_cents",
            0,
        ),
        (
            ProductSkuInput,
            {"sku_code": "SKU-1", "name": "Default", "price_cents": 0},
            "market_price_cents",
            0,
        ),
        (
            ProductSkuInput,
            {"sku_code": "SKU-1", "name": "Default", "price_cents": 0},
            "stock_quantity",
            0,
        ),
        (
            ProductSkuInput,
            {"sku_code": "SKU-1", "name": "Default", "price_cents": 0},
            "sort_order",
            POSTGRES_INTEGER_MIN,
        ),
        (
            ProductCreate,
            {"product_code": "P-1", "name": "Product", "category_id": 1, "base_price_cents": 0},
            "category_id",
            1,
        ),
        (
            ProductCreate,
            {"product_code": "P-1", "name": "Product", "category_id": 1, "base_price_cents": 0},
            "base_price_cents",
            0,
        ),
        (
            ProductCreate,
            {"product_code": "P-1", "name": "Product", "category_id": 1, "base_price_cents": 0},
            "market_price_cents",
            0,
        ),
        (
            ProductCreate,
            {"product_code": "P-1", "name": "Product", "category_id": 1, "base_price_cents": 0},
            "inventory_count",
            0,
        ),
        (
            ProductCreate,
            {"product_code": "P-1", "name": "Product", "category_id": 1, "base_price_cents": 0},
            "sort_order",
            POSTGRES_INTEGER_MIN,
        ),
        (ProductUpdate, {}, "category_id", 1),
        (ProductUpdate, {}, "base_price_cents", 0),
        (ProductUpdate, {}, "market_price_cents", 0),
        (ProductUpdate, {}, "inventory_count", 0),
        (ProductUpdate, {}, "sort_order", POSTGRES_INTEGER_MIN),
        (ImageUpdate, {}, "sort_order", POSTGRES_INTEGER_MIN),
    ],
)
def test_postgres_integer_inputs_enforce_signed_int32_bounds(
    model_type: type[BaseModel],
    base_payload: dict[str, Any],
    field_name: str,
    minimum: int,
) -> None:
    minimum_payload = {**base_payload, field_name: minimum}
    maximum_payload = {**base_payload, field_name: POSTGRES_INTEGER_MAX}

    assert getattr(model_type.model_validate(minimum_payload), field_name) == minimum
    assert getattr(model_type.model_validate(maximum_payload), field_name) == POSTGRES_INTEGER_MAX

    with pytest.raises(ValidationError):
        model_type.model_validate({**base_payload, field_name: minimum - 1})
    with pytest.raises(ValidationError):
        model_type.model_validate({**base_payload, field_name: POSTGRES_INTEGER_MAX + 1})


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/v1/admin/categories/1", {"name": None}),
        ("/api/v1/admin/products/1", {"name": None}),
        ("/api/v1/admin/products/1/images/1", {"sort_order": None}),
    ],
)
def test_explicit_null_is_reported_as_http_422(
    admin_client: TestClient,
    path: str,
    payload: dict[str, None],
) -> None:
    response = admin_client.patch(path, json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_build_engine_enables_and_enforces_sqlite_foreign_keys(settings: Settings) -> None:
    engine = build_engine(settings)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            connection.exec_driver_sql("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE child (parent_id INTEGER NOT NULL REFERENCES parent(id))"
            )
            with pytest.raises(IntegrityError):
                connection.exec_driver_sql("INSERT INTO child (parent_id) VALUES (999)")
    finally:
        engine.dispose()
