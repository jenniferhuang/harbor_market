from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.models import Category, MiniCouponClaim, Product, ProductSku, ShopCoupon
from app.schemas.shop_commerce import MAX_AMOUNT_CENTS
from app.services import shop_cart
from app.wechat.auth import WechatIdentity

QUOTE = "/api/v1/mini/shop/cart/quote"
PUBLIC_COUPONS = "/api/v1/shop/coupons"


class CartWechatProvider:
    def exchange_code(self, code: str) -> WechatIdentity:
        return WechatIdentity(app_id="wx1234567890abcdef", openid=f"cart-{code}")


@pytest.fixture
def cart_customers(app: FastAPI, client: TestClient) -> list[dict[str, Any]]:
    app.state.wechat_auth_provider = CartWechatProvider()
    customers = []
    for code in ("first-cart-customer", "second-cart-customer"):
        response = client.post("/api/v1/mini/auth/login", json={"code": code})
        assert response.status_code == 200, response.text
        customers.append(response.json()["data"])
    return customers


@pytest.fixture
def cart_catalog(app: FastAPI) -> dict[str, int]:
    with app.state.session_factory() as session:
        category = Category(code="CART", name="饮品")
        session.add(category)
        coffee = Product(
            product_code="COFFEE",
            name="咖啡",
            category=category,
            status="published",
            base_price_cents=999,
            inventory_count=0,
            specifications=[
                {
                    "code": "cup",
                    "name": "杯型",
                    "required": True,
                    "min_select": 1,
                    "max_select": 1,
                    "options": [
                        {"code": "small", "name": "小杯", "is_default": True},
                        {"code": "large", "name": "大杯", "price_delta_cents": 300},
                    ],
                },
                {
                    "code": "addons",
                    "name": "加料",
                    "selection_mode": "multiple",
                    "max_select": 2,
                    "options": [
                        {"code": "pearls", "name": "珍珠", "price_delta_cents": 50},
                        {"code": "milk", "name": "奶", "price_delta_cents": 100},
                        {"code": "reduction", "name": "减料", "price_delta_cents": -1000},
                    ],
                },
            ],
        )
        plain = Product(
            product_code="PLAIN",
            name="原味饮品",
            category=category,
            status="published",
            base_price_cents=999,
        )
        session.add_all([coffee, plain])
        session.flush()
        small = ProductSku(
            product=coffee,
            sku_code="COFFEE-SMALL",
            name="小杯",
            price_cents=400,
            stock_quantity=10,
            attributes={"cup": "small"},
            is_default=True,
        )
        large = ProductSku(
            product=coffee,
            sku_code="COFFEE-LARGE",
            name="大杯",
            price_cents=550,
            stock_quantity=10,
            attributes={"cup": "large"},
        )
        inactive = ProductSku(
            product=coffee,
            sku_code="COFFEE-INACTIVE",
            name="停用规格",
            price_cents=1,
            stock_quantity=100,
            attributes={"cup": "large"},
            is_active=False,
        )
        plain_sku = ProductSku(
            product=plain,
            sku_code="PLAIN-DEFAULT",
            name="默认规格",
            price_cents=250,
            stock_quantity=200,
            is_default=True,
        )
        session.add_all([small, large, inactive, plain_sku])
        session.commit()
        return {
            "category": category.id,
            "coffee": coffee.id,
            "plain": plain.id,
            "small": small.id,
            "large": large.id,
            "inactive": inactive.id,
            "plain_sku": plain_sku.id,
        }


def _headers(customer: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {customer['access_token']}"}


def _item(**changes: Any) -> dict[str, Any]:
    return {
        "product_code": "COFFEE",
        "sku_code": "COFFEE-LARGE",
        "selections": {"cup": ["large"], "addons": ["pearls", "milk"]},
        "quantity": 2,
        **changes,
    }


def _coupon(app: FastAPI, customer_id: int | None, **changes: Any) -> int:
    now = datetime.now(UTC)
    with app.state.session_factory() as session:
        coupon = ShopCoupon(
            title="满10元减2元",
            min_spend_cents=1000,
            discount_cents=200,
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=1),
        )
        for name, value in changes.items():
            setattr(coupon, name, value)
        session.add(coupon)
        session.flush()
        if customer_id is not None:
            session.add(
                MiniCouponClaim(customer_id=customer_id, coupon_id=coupon.id, claimed_at=now)
            )
        session.commit()
        return coupon.id


def _assert_error(response: Any, code: str | None = None, status: int = 422) -> None:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    if code is not None:
        assert error["code"] == code
    assert any("\u4e00" <= character <= "\u9fff" for character in error["message"])
    assert response.headers.get("cache-control") == "private, no-store"


def test_quote_uses_absolute_sku_and_multiple_addons_and_is_read_only(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
) -> None:
    customer = cart_customers[0]
    coupon_id = _coupon(app, customer["customer"]["id"])
    statements: list[str] = []

    def record_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(app.state.engine, "before_cursor_execute", record_statement)
    try:
        for _ in range(2):
            response = client.post(QUOTE, headers=_headers(customer), json={"items": [_item()]})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert "Authorization" in response.headers["vary"]
            assert response.json()["data"] == {
                "items": [
                    {
                        **_item(),
                        "name": "咖啡",
                        "sku_name": "大杯",
                        "unit_price_cents": 700,
                        "line_total_cents": 1400,
                    }
                ],
                "subtotal_cents": 1400,
                "discount_cents": 0,
                "total_cents": 1400,
                "coupon_id": None,
                "coupon_evaluations": [
                    {
                        "coupon_id": coupon_id,
                        "title": "满10元减2元",
                        "min_spend_cents": 1000,
                        "discount_cents": 200,
                        "eligible": True,
                        "shortfall_cents": 0,
                        "reason": "eligible",
                    }
                ],
            }
    finally:
        event.remove(app.state.engine, "before_cursor_execute", record_statement)
    assert statements and set(statements) == {"SELECT"}


def test_quote_reloads_current_sku_price_and_option_deltas(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
) -> None:
    headers = _headers(cart_customers[0])
    assert (
        client.post(QUOTE, headers=headers, json={"items": [_item()]}).json()["data"][
            "subtotal_cents"
        ]
        == 1400
    )
    with app.state.session_factory() as session:
        sku = session.get(ProductSku, cart_catalog["large"])
        assert sku is not None
        sku.price_cents = 650
        product = session.get(Product, cart_catalog["coffee"])
        assert product is not None
        specifications = product.specifications.copy()
        specifications[1] = {
            **specifications[1],
            "options": [
                {**option, "price_delta_cents": 200} if option["code"] == "milk" else option
                for option in specifications[1]["options"]
            ],
        }
        product.specifications = specifications
        product.name = "新名称"
        session.commit()
    result = client.post(QUOTE, headers=headers, json={"items": [_item()]}).json()["data"]
    assert result["subtotal_cents"] == 1800
    assert result["items"][0]["name"] == "新名称"


def test_negative_addon_clamps_unit_price_and_empty_cart_returns_zero(
    client: TestClient,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
) -> None:
    headers = _headers(cart_customers[0])
    result = client.post(
        QUOTE,
        headers=headers,
        json={"items": [_item(selections={"cup": ["large"], "addons": ["reduction"]})]},
    ).json()["data"]
    assert result["items"][0]["unit_price_cents"] == 0
    assert result["subtotal_cents"] == result["total_cents"] == 0
    assert client.post(QUOTE, headers=headers, json={"items": []}).json() == {
        "data": {
            "items": [],
            "subtotal_cents": 0,
            "discount_cents": 0,
            "total_cents": 0,
            "coupon_id": None,
            "coupon_evaluations": [],
        }
    }


@pytest.mark.parametrize(
    "selections,sku_code",
    [
        ({}, "COFFEE-SMALL"),
        ({"cup": ["missing"]}, "COFFEE-LARGE"),
        ({"cup": ["small", "large"]}, "COFFEE-LARGE"),
        ({"cup": ["large"], "unknown": []}, "COFFEE-LARGE"),
        ({"cup": ["large"], "addons": ["pearls", "milk", "reduction"]}, "COFFEE-LARGE"),
        ({"cup": ["small"]}, "COFFEE-LARGE"),
        ({"cup": ["large"]}, "COFFEE-INACTIVE"),
        ({"cup": ["large"]}, "PLAIN-DEFAULT"),
    ],
)
def test_invalid_selections_and_sku_references_are_rejected(
    client: TestClient,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
    selections: dict[str, list[str]],
    sku_code: str,
) -> None:
    _assert_error(
        client.post(
            QUOTE,
            headers=_headers(cart_customers[0]),
            json={"items": [_item(selections=selections, sku_code=sku_code)]},
        ),
        "cart_item_unavailable",
    )


@pytest.mark.parametrize(
    "change", ["draft", "archived", "category", "out_of_stock", "sku", "zero_stock"]
)
def test_current_publication_and_stock_rules_are_enforced(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
    change: str,
) -> None:
    with app.state.session_factory() as session:
        product = session.get(Product, cart_catalog["coffee"])
        sku = session.get(ProductSku, cart_catalog["large"])
        category = session.get(Category, cart_catalog["category"])
        assert product and sku and category
        if change in {"draft", "archived"}:
            product.status = change
        elif change == "category":
            category.is_active = False
        elif change == "out_of_stock":
            product.stock_status = "out_of_stock"
        elif change == "sku":
            sku.is_active = False
        else:
            sku.stock_quantity = 0
        session.commit()
    response = client.post(QUOTE, headers=_headers(cart_customers[0]), json={"items": [_item()]})
    _assert_error(response)


def test_stock_is_aggregated_across_customizations_and_preorder_uses_sku_stock(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
) -> None:
    with app.state.session_factory() as session:
        product = session.get(Product, cart_catalog["coffee"])
        assert product
        product.stock_status = "preorder"
        session.commit()
    headers = _headers(cart_customers[0])
    items = [_item(quantity=5), _item(quantity=5, selections={"cup": ["large"]})]
    assert client.post(QUOTE, headers=headers, json={"items": items}).status_code == 200
    items[1]["quantity"] = 6
    _assert_error(
        client.post(QUOTE, headers=headers, json={"items": items}), "cart_stock_insufficient"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("quantity", True),
        ("quantity", "2"),
        ("quantity", 1.5),
        ("quantity", 0),
        ("quantity", 100),
        ("product_code", "x" * 65),
        ("sku_code", "x" * 81),
        ("unit_price_cents", 1),
        ("line_total_cents", 1),
        ("name", "伪造名称"),
        ("selections", {"cup": ["large", "large"]}),
        ("selections", {"cup": ["x" * 65]}),
        ("selections", {f"spec{i}": [] for i in range(21)}),
        ("selections", {"addons": [f"option{i}" for i in range(51)]}),
    ],
)
def test_quote_rejects_tampering_and_bounded_request_violations(
    client: TestClient,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
    field: str,
    value: Any,
) -> None:
    _assert_error(
        client.post(
            QUOTE, headers=_headers(cart_customers[0]), json={"items": [_item(**{field: value})]}
        ),
        "validation_error",
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"items": [], "subtotal_cents": 0},
        {"items": [], "customer_id": 1},
        {"items": [], "coupon_id": True},
        {"items": [], "coupon_id": "1"},
        {"items": [_item()] * 101},
    ],
)
def test_quote_rejects_client_totals_identity_and_oversized_cart(
    client: TestClient,
    cart_customers: list[dict[str, Any]],
    payload: dict[str, Any],
) -> None:
    _assert_error(
        client.post(QUOTE, headers=_headers(cart_customers[0]), json=payload), "validation_error"
    )


def test_quote_requires_mini_bearer_and_rejects_revoked_session(
    client: TestClient,
    admin_client: TestClient,
    cart_customers: list[dict[str, Any]],
) -> None:
    _assert_error(admin_client.post(QUOTE, json={"items": []}), status=401)
    headers = _headers(cart_customers[0])
    assert client.post("/api/v1/mini/auth/logout", headers=headers).status_code == 200
    _assert_error(client.post(QUOTE, headers=headers, json={"items": []}), status=401)


def test_coupon_threshold_uses_only_requested_lines_and_applies_explicit_selection(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
) -> None:
    customer = cart_customers[0]
    coupon_id = _coupon(app, customer["customer"]["id"])
    headers = _headers(customer)
    below = {"items": [_item(quantity=1)]}
    result = client.post(QUOTE, headers=headers, json=below).json()["data"]
    assert result["coupon_evaluations"][0]["shortfall_cents"] == 300
    assert result["coupon_evaluations"][0]["reason"] == "below_minimum"
    _assert_error(
        client.post(QUOTE, headers=headers, json={**below, "coupon_id": coupon_id}),
        "cart_coupon_ineligible",
    )
    exact = {
        "items": [_item(sku_code="COFFEE-SMALL", selections={"cup": ["small"], "addons": ["milk"]})]
    }
    result = client.post(QUOTE, headers=headers, json={**exact, "coupon_id": coupon_id}).json()[
        "data"
    ]
    assert (result["subtotal_cents"], result["discount_cents"], result["total_cents"]) == (
        1000,
        200,
        800,
    )
    assert result["coupon_id"] == coupon_id


@pytest.mark.parametrize("state", ["inactive", "not_started", "expired"])
def test_owned_coupon_history_is_evaluated_but_invalid_window_cannot_apply(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
    state: str,
) -> None:
    now = datetime.now(UTC)
    changes = {
        "inactive": {"is_active": False},
        "not_started": {"starts_at": now + timedelta(hours=1)},
        "expired": {"starts_at": now - timedelta(days=2), "expires_at": now - timedelta(hours=1)},
    }[state]
    customer = cart_customers[0]
    coupon_id = _coupon(app, customer["customer"]["id"], **changes)
    headers = _headers(customer)
    payload = {"items": [_item()]}
    result = client.post(QUOTE, headers=headers, json=payload).json()["data"]
    assert result["coupon_evaluations"][0]["eligible"] is False
    assert result["coupon_evaluations"][0]["reason"] == state
    _assert_error(
        client.post(QUOTE, headers=headers, json={**payload, "coupon_id": coupon_id}),
        "cart_coupon_ineligible",
    )


def test_coupon_current_window_is_start_inclusive_expiry_exclusive(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)

    class FixedClock:
        @staticmethod
        def now(_timezone: Any) -> datetime:
            return fixed_now

    monkeypatch.setattr(shop_cart, "datetime", FixedClock)
    customer = cart_customers[0]
    active = _coupon(
        app,
        customer["customer"]["id"],
        starts_at=fixed_now,
        expires_at=fixed_now + timedelta(hours=1),
    )
    expired = _coupon(
        app,
        customer["customer"]["id"],
        starts_at=fixed_now - timedelta(hours=1),
        expires_at=fixed_now,
    )
    headers = _headers(customer)
    assert (
        client.post(
            QUOTE, headers=headers, json={"items": [_item()], "coupon_id": active}
        ).status_code
        == 200
    )
    _assert_error(
        client.post(QUOTE, headers=headers, json={"items": [_item()], "coupon_id": expired}),
        "cart_coupon_ineligible",
    )


def test_unclaimed_and_other_customers_coupons_are_not_visible_or_applicable(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
) -> None:
    own_id = _coupon(app, cart_customers[0]["customer"]["id"])
    other_id = _coupon(app, cart_customers[1]["customer"]["id"])
    unclaimed_id = _coupon(app, None)
    headers = _headers(cart_customers[0])
    result = client.post(QUOTE, headers=headers, json={"items": [_item()]}).json()["data"]
    assert [coupon["coupon_id"] for coupon in result["coupon_evaluations"]] == [own_id]
    for coupon_id in (other_id, unclaimed_id, MAX_AMOUNT_CENTS):
        _assert_error(
            client.post(QUOTE, headers=headers, json={"items": [_item()], "coupon_id": coupon_id}),
            "cart_coupon_invalid",
        )


def test_coupon_evaluations_are_bounded_and_selected_old_claim_is_included(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
) -> None:
    customer = cart_customers[0]
    ids = [_coupon(app, customer["customer"]["id"]) for _ in range(102)]
    # Equal timestamps exercise the claim-ID tie breaker.
    with app.state.session_factory() as session:
        for claim in session.scalars(select(MiniCouponClaim)):
            claim.claimed_at = datetime(2026, 1, 1, tzinfo=UTC)
        session.commit()
    headers = _headers(customer)
    payload = {"items": [_item()]}
    result = client.post(QUOTE, headers=headers, json=payload).json()["data"]
    assert [coupon["coupon_id"] for coupon in result["coupon_evaluations"]] == ids[::-1][:100]
    selected = client.post(QUOTE, headers=headers, json={**payload, "coupon_id": ids[0]}).json()[
        "data"
    ]
    assert len(selected["coupon_evaluations"]) == 100
    assert selected["coupon_evaluations"][-1]["coupon_id"] == ids[0]
    assert selected["discount_cents"] == 200


@pytest.mark.parametrize("mode", ["unit", "line", "subtotal"])
def test_quote_money_overflow_is_rejected_before_response(
    client: TestClient,
    app: FastAPI,
    cart_customers: list[dict[str, Any]],
    cart_catalog: dict[str, int],
    mode: str,
) -> None:
    with app.state.session_factory() as session:
        sku = session.get(ProductSku, cart_catalog["large"])
        assert sku
        sku.price_cents = MAX_AMOUNT_CENTS
        session.commit()
    item = _item(quantity=1, selections={"cup": ["large"]})
    items = [item]
    if mode == "unit":
        item["selections"]["addons"] = ["milk"]
    elif mode == "line":
        item["quantity"] = 2
    else:
        items.append({**item, "selections": {"cup": ["large"]}})
    _assert_error(
        client.post(QUOTE, headers=_headers(cart_customers[0]), json={"items": items}),
        "cart_amount_exceeded",
    )


def test_public_coupon_center_paginates_all_current_offers_without_authentication(
    client: TestClient,
    app: FastAPI,
) -> None:
    ids = [_coupon(app, None, title=f"优惠券{i}", discount_cents=100 + i) for i in range(23)]
    now = datetime.now(UTC)
    _coupon(app, None, is_active=False)
    _coupon(app, None, starts_at=now + timedelta(hours=1))
    _coupon(app, None, expires_at=now - timedelta(hours=1))
    first = client.get(PUBLIC_COUPONS)
    second = client.get(PUBLIC_COUPONS, params={"page": 2})
    assert first.status_code == second.status_code == 200
    assert [row["id"] for row in first.json()["data"] + second.json()["data"]] == ids[::-1]
    assert len(first.json()["data"]) == 20
    assert set(first.json()["data"][0]) == {
        "id",
        "title",
        "min_spend_cents",
        "discount_cents",
        "starts_at",
        "expires_at",
    }
    assert len(client.get("/api/v1/shop/home").json()["data"]["coupons"]) == 20
    for parameters in ({"page": 0}, {"page": 1_000_001}, {"page_size": 101}):
        response = client.get(PUBLIC_COUPONS, params=parameters)
        assert response.status_code == 422
        assert "error" in response.json()
