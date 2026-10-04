from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import (
    Category,
    MiniCouponClaim,
    MiniFavorite,
    Product,
    ProductSku,
    ShopOrder,
    ShopOrderLine,
)
from app.wechat.auth import WechatIdentity

MINI = "/api/v1/mini/shop"
ADMIN = "/api/v1/admin/shop"
COMPLETED_AT = "2025-07-10T08:30:00Z"


class CommerceWechatProvider:
    def exchange_code(self, code: str) -> WechatIdentity:
        return WechatIdentity(app_id="wx1234567890abcdef", openid=f"commerce-{code}")


@pytest.fixture
def commerce_customers(app: FastAPI, client: TestClient) -> list[dict[str, Any]]:
    app.state.wechat_auth_provider = CommerceWechatProvider()
    result = []
    for code in ("first-shop-customer", "second-shop-customer"):
        response = client.post("/api/v1/mini/auth/login", json={"code": code})
        assert response.status_code == 200, response.text
        result.append(response.json()["data"])
    return result


@pytest.fixture
def commerce_products(app: FastAPI) -> dict[str, int]:
    with app.state.session_factory() as session:
        active = Category(code="COMMERCE", name="咖啡")
        inactive = Category(code="INACTIVE", name="停用分类", is_active=False)
        session.add_all([active, inactive])
        session.flush()
        result = {}
        for code, status, category in (
            ("PUBLIC-1", "published", active),
            ("PUBLIC-2", "published", active),
            ("DRAFT-1", "draft", active),
            ("ARCHIVED-1", "archived", active),
            ("HIDDEN-1", "published", inactive),
        ):
            product = Product(
                product_code=code,
                name=f"原始商品 {code}",
                category=category,
                status=status,
                base_price_cents=1990,
                inventory_count=37,
            )
            session.add(product)
            session.flush()
            result[code] = product.id
        session.add_all(
            [
                ProductSku(
                    product_id=result["PUBLIC-1"],
                    sku_code="PUBLIC-ACTIVE",
                    name="有效规格",
                    price_cents=1990,
                    is_active=True,
                    is_default=True,
                ),
                ProductSku(
                    product_id=result["PUBLIC-1"],
                    sku_code="PUBLIC-INACTIVE",
                    name="停用规格",
                    price_cents=1990,
                    is_active=False,
                ),
            ]
        )
        session.commit()
    return result


def _bearer(customer: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {customer['access_token']}"}


def _coupon(client: TestClient, **changes: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    payload = {
        "title": "满 50 减 10",
        "min_spend_cents": 5000,
        "discount_cents": 1000,
        "starts_at": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        **changes,
    }
    response = client.post(f"{ADMIN}/coupons", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _order(customer_id: int | None = None, **changes: Any) -> dict[str, Any]:
    return {
        "external_reference": "offline-receipt-001",
        "customer_id": customer_id,
        "completed_at": COMPLETED_AT,
        "items": [
            {"product_code": "public-1", "quantity": 2, "unit_price_cents": 1500},
            {"product_code": "PUBLIC-2", "quantity": 1, "unit_price_cents": 990},
        ],
        **changes,
    }


def _assert_chinese_error(response: Any, expected_status: int) -> None:
    assert response.status_code == expected_status, response.text
    assert "error" in response.json()
    assert any(
        "\u4e00" <= character <= "\u9fff" for character in response.json()["error"]["message"]
    )


def test_favorites_are_idempotent_private_and_customer_scoped(
    admin_client: TestClient,
    commerce_customers: list[dict[str, Any]],
    commerce_products: dict[str, int],
    app: FastAPI,
) -> None:
    first, second = commerce_customers
    first_headers, second_headers = _bearer(first), _bearer(second)
    path = f"{MINI}/favorites/public-1"
    assert admin_client.get(path, headers=first_headers).json() == {"data": {"is_favorite": False}}
    for _ in range(2):
        response = admin_client.put(path, headers=first_headers)
        assert response.json() == {"data": {"is_favorite": True}}
        assert response.headers["cache-control"] == "private, no-store"
    assert admin_client.get(path, headers=second_headers).json() == {"data": {"is_favorite": False}}
    listing = admin_client.get(f"{MINI}/favorites", headers=first_headers)
    assert [product["product_code"] for product in listing.json()["data"]] == ["PUBLIC-1"]
    assert [sku["sku_code"] for sku in listing.json()["data"][0]["skus"]] == ["PUBLIC-ACTIVE"]
    assert admin_client.get(f"{MINI}/favorites", headers=second_headers).json() == {"data": []}
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MiniFavorite)) == 1
    for _ in range(2):
        assert admin_client.delete(path, headers=first_headers).json() == {
            "data": {"is_favorite": False}
        }


@pytest.mark.parametrize("code", ["DRAFT-1", "ARCHIVED-1", "HIDDEN-1", "MISSING"])
def test_favorite_mutations_and_lookup_exclude_nonpublic_products(
    client: TestClient,
    commerce_customers: list[dict[str, Any]],
    commerce_products: dict[str, int],
    code: str,
) -> None:
    for method in ("GET", "PUT", "DELETE"):
        response = client.request(
            method, f"{MINI}/favorites/{code}", headers=_bearer(commerce_customers[0])
        )
        _assert_chinese_error(response, 404)


def test_favorites_disappear_when_product_or_category_is_unpublished(
    client: TestClient,
    commerce_customers: list[dict[str, Any]],
    commerce_products: dict[str, int],
    app: FastAPI,
) -> None:
    headers = _bearer(commerce_customers[0])
    for code in ("PUBLIC-1", "PUBLIC-2"):
        assert client.put(f"{MINI}/favorites/{code}", headers=headers).status_code == 200
    with app.state.session_factory() as session:
        product = session.get(Product, commerce_products["PUBLIC-1"])
        assert product is not None
        product.status = "draft"
        session.commit()
    assert [
        item["product_code"]
        for item in client.get(f"{MINI}/favorites", headers=headers).json()["data"]
    ] == ["PUBLIC-2"]
    with app.state.session_factory() as session:
        category = session.scalar(select(Category).where(Category.code == "COMMERCE"))
        assert category is not None
        category.is_active = False
        session.commit()
    assert client.get(f"{MINI}/favorites", headers=headers).json() == {"data": []}


def test_coupon_claims_are_unique_and_derive_customer_from_bearer(
    admin_client: TestClient,
    commerce_customers: list[dict[str, Any]],
    app: FastAPI,
) -> None:
    coupon = _coupon(admin_client)
    first, second = commerce_customers
    path = f"{MINI}/coupons/{coupon['id']}/claim"
    listing = admin_client.get(f"{MINI}/coupons", headers=_bearer(first)).json()["data"]
    assert len(listing) == 1
    assert listing[0]["coupon_id"] == coupon["id"]
    assert listing[0]["is_active"] is True
    assert listing[0]["claimed_at"] is None
    claim = admin_client.post(path, headers=_bearer(first))
    assert claim.status_code == 200, claim.text
    assert claim.json()["data"]["claimed_at"] is not None
    assert admin_client.post(path, headers=_bearer(first)).json() == claim.json()
    assert admin_client.get(f"{MINI}/coupons", headers=_bearer(first)).json()["data"] == [
        claim.json()["data"]
    ]
    assert (
        admin_client.get(f"{MINI}/coupons", headers=_bearer(second)).json()["data"][0]["claimed_at"]
        is None
    )
    forged = admin_client.post(
        path, headers=_bearer(second), json={"customer_id": first["customer"]["id"]}
    )
    _assert_chinese_error(forged, 422)
    assert admin_client.post(path, headers=_bearer(second)).status_code == 200
    with app.state.session_factory() as session:
        claims = list(session.scalars(select(MiniCouponClaim)))
        assert len(claims) == 2
        assert {item.customer_id for item in claims} == {
            item["customer"]["id"] for item in commerce_customers
        }
    assert "openid" not in claim.text
    assert "access_token" not in claim.text


@pytest.mark.parametrize("window", ["expired", "future", "inactive"])
def test_only_active_current_coupons_can_be_listed_and_claimed(
    admin_client: TestClient,
    commerce_customers: list[dict[str, Any]],
    window: str,
) -> None:
    now = datetime.now(UTC)
    changes: dict[str, Any] = {}
    if window == "expired":
        changes = {
            "starts_at": (now - timedelta(days=2)).isoformat(),
            "expires_at": (now - timedelta(days=1)).isoformat(),
        }
    elif window == "future":
        changes = {
            "starts_at": (now + timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=2)).isoformat(),
        }
    else:
        changes = {"is_active": False}
    coupon = _coupon(admin_client, **changes)
    headers = _bearer(commerce_customers[0])
    assert admin_client.get(f"{MINI}/coupons", headers=headers).json() == {"data": []}
    _assert_chinese_error(
        admin_client.post(f"{MINI}/coupons/{coupon['id']}/claim", headers=headers), 404
    )


@pytest.mark.parametrize("window", ["expired", "inactive"])
def test_existing_coupon_claim_retries_survive_expiry_and_deactivation(
    admin_client: TestClient,
    commerce_customers: list[dict[str, Any]],
    app: FastAPI,
    window: str,
) -> None:
    coupon = _coupon(admin_client)
    first, second = commerce_customers
    path = f"{MINI}/coupons/{coupon['id']}/claim"
    original = admin_client.post(path, headers=_bearer(first))
    assert original.status_code == 200, original.text
    changes = (
        {"expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
        if window == "expired"
        else {"is_active": False}
    )
    updated = admin_client.patch(f"{ADMIN}/coupons/{coupon['id']}", json=changes)
    assert updated.status_code == 200, updated.text

    retry = admin_client.post(path, headers=_bearer(first))
    assert retry.status_code == 200, retry.text
    assert retry.json()["data"]["coupon_id"] == coupon["id"]
    assert retry.json()["data"]["claimed_at"] == original.json()["data"]["claimed_at"]
    assert retry.json()["data"]["is_active"] == updated.json()["data"]["is_active"]
    assert retry.json()["data"]["expires_at"] == updated.json()["data"]["expires_at"]
    _assert_chinese_error(admin_client.post(path, headers=_bearer(second)), 404)
    assert admin_client.get(f"{MINI}/coupons", headers=_bearer(first)).json() == {"data": []}
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MiniCouponClaim)) == 1


def test_coupon_admin_updates_validate_merged_definition_and_deactivation(
    admin_client: TestClient,
    commerce_customers: list[dict[str, Any]],
) -> None:
    coupon = _coupon(admin_client)
    path = f"{ADMIN}/coupons/{coupon['id']}"
    for payload in (
        {"discount_cents": 6000},
        {"expires_at": coupon["starts_at"]},
        {"title": None},
        {"is_active": "false"},
    ):
        _assert_chinese_error(admin_client.patch(path, json=payload), 422)
    assert admin_client.get(f"{ADMIN}/coupons").json()["data"] == [coupon]
    response = admin_client.patch(path, json={"title": "新满减标题", "is_active": False})
    assert response.status_code == 200
    assert response.json()["data"]["title"] == "新满减标题"
    assert admin_client.get(f"{MINI}/coupons", headers=_bearer(commerce_customers[0])).json() == {
        "data": []
    }


def test_historical_order_totals_snapshots_replay_and_customer_scope(
    admin_client: TestClient,
    commerce_customers: list[dict[str, Any]],
    commerce_products: dict[str, int],
    app: FastAPI,
) -> None:
    first, second = commerce_customers
    payload = _order(first["customer"]["id"])
    response = admin_client.post(f"{ADMIN}/orders", json=payload)
    assert response.status_code == 200, response.text
    recorded = response.json()["data"]
    assert recorded["total_cents"] == 3990
    assert recorded["status"] == "completed"
    assert recorded["items"][0] == {
        "product_code": "PUBLIC-1",
        "product_name": "原始商品 PUBLIC-1",
        "unit_price_cents": 1500,
        "quantity": 2,
        "line_total_cents": 3000,
    }
    with app.state.session_factory() as session:
        product = session.get(Product, commerce_products["PUBLIC-1"])
        assert product is not None
        assert product.inventory_count == 37
        product.name = "当前商品新名称"
        product.base_price_cents = 9900
        session.commit()
    replay = admin_client.post(
        f"{ADMIN}/orders", json={**payload, "items": list(reversed(payload["items"]))}
    )
    assert replay.json() == response.json()
    assert admin_client.get(f"{MINI}/orders", headers=_bearer(first)).json() == {"data": [recorded]}
    assert admin_client.get(f"{MINI}/orders", headers=_bearer(second)).json() == {"data": []}
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ShopOrder)) == 1
        assert session.scalar(select(func.count()).select_from(ShopOrderLine)) == 2


def test_anonymous_historical_orders_default_time_and_replay_without_new_rows(
    admin_client: TestClient,
    commerce_products: dict[str, int],
    commerce_customers: list[dict[str, Any]],
) -> None:
    payload = _order()
    payload.pop("completed_at")
    payload.pop("customer_id")
    first = admin_client.post(f"{ADMIN}/orders", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["data"]["customer_id"] is None
    assert datetime.fromisoformat(
        first.json()["data"]["completed_at"].replace("Z", "+00:00")
    ) <= datetime.now(UTC)
    assert admin_client.post(f"{ADMIN}/orders", json=payload).json() == first.json()
    for customer in commerce_customers:
        assert admin_client.get(f"{MINI}/orders", headers=_bearer(customer)).json() == {"data": []}


@pytest.mark.parametrize("changed_field", ["items", "customer_id", "completed_at"])
def test_conflicting_historical_reference_is_rejected_without_duplicate_orders(
    admin_client: TestClient,
    commerce_products: dict[str, int],
    commerce_customers: list[dict[str, Any]],
    app: FastAPI,
    changed_field: str,
) -> None:
    payload = _order(commerce_customers[0]["customer"]["id"])
    assert admin_client.post(f"{ADMIN}/orders", json=payload).status_code == 200
    changes = {
        "items": [{"product_code": "PUBLIC-1", "quantity": 3, "unit_price_cents": 1500}],
        "customer_id": commerce_customers[1]["customer"]["id"],
        "completed_at": "2025-07-11T08:30:00Z",
    }
    response = admin_client.post(
        f"{ADMIN}/orders", json={**payload, changed_field: changes[changed_field]}
    )
    _assert_chinese_error(response, 409)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ShopOrder)) == 1


def test_historical_products_may_be_archived_and_snapshots_survive_product_deletion(
    admin_client: TestClient,
    commerce_products: dict[str, int],
    app: FastAPI,
) -> None:
    payload = _order(items=[{"product_code": "ARCHIVED-1", "quantity": 1, "unit_price_cents": 1}])
    response = admin_client.post(f"{ADMIN}/orders", json=payload)
    assert response.status_code == 200, response.text
    with app.state.session_factory() as session:
        product = session.get(Product, commerce_products["ARCHIVED-1"])
        assert product is not None
        session.delete(product)
        session.commit()
        item = session.scalar(select(ShopOrderLine))
        assert item is not None and item.product_id is None
    assert admin_client.post(f"{ADMIN}/orders", json=payload).json() == response.json()


def test_order_void_is_idempotent_and_replay_never_restores_completion(
    admin_client: TestClient,
    commerce_products: dict[str, int],
    commerce_customers: list[dict[str, Any]],
    app: FastAPI,
) -> None:
    payload = _order(commerce_customers[0]["customer"]["id"])
    recorded = admin_client.post(f"{ADMIN}/orders", json=payload).json()["data"]
    path = f"{ADMIN}/orders/{recorded['id']}/void"
    voided = admin_client.post(path)
    assert voided.status_code == 200, voided.text
    assert voided.json()["data"]["status"] == "voided"
    assert voided.json()["data"]["total_cents"] == recorded["total_cents"]
    assert admin_client.post(path).json() == voided.json()
    assert admin_client.post(f"{ADMIN}/orders", json=payload).json() == voided.json()
    with app.state.session_factory() as session:
        order = session.get(ShopOrder, recorded["id"])
        assert order is not None and order.voided_at is not None
    assert admin_client.get(f"{MINI}/orders", headers=_bearer(commerce_customers[0])).json() == {
        "data": [voided.json()["data"]]
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"total_cents": 1},
        {"total_amount_cents": 1},
        {"status": "completed"},
        {"openid": "forged"},
        {"completed_at": "2025-01-01T00:00:00"},
        {"completed_at": "2099-01-01T00:00:00Z"},
        {"items": []},
        {"items": [{"product_code": "PUBLIC-1", "quantity": True, "unit_price_cents": 1}]},
        {"items": [{"product_code": "PUBLIC-1", "quantity": 0, "unit_price_cents": 1}]},
        {"items": [{"product_code": "PUBLIC-1", "quantity": 100001, "unit_price_cents": 1}]},
        {"items": [{"product_code": "PUBLIC-1", "quantity": 2, "unit_price_cents": 2147483647}]},
        {"items": [{"product_code": "PUBLIC-1", "quantity": 1, "unit_price_cents": -1}]},
        {
            "items": [
                {
                    "product_code": "PUBLIC-1",
                    "quantity": 1,
                    "unit_price_cents": 1,
                    "product_name": "forged",
                }
            ]
        },
    ],
)
def test_invalid_historical_orders_do_not_create_records(
    admin_client: TestClient,
    commerce_products: dict[str, int],
    app: FastAPI,
    changes: dict[str, Any],
) -> None:
    response = admin_client.post(f"{ADMIN}/orders", json=_order(**changes))
    _assert_chinese_error(response, 422)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ShopOrder)) == 0


def test_commerce_lists_bound_pagination_and_return_arrays(
    admin_client: TestClient,
    commerce_customers: list[dict[str, Any]],
) -> None:
    for path in (
        f"{ADMIN}/coupons",
        f"{ADMIN}/orders",
        f"{MINI}/coupons",
        f"{MINI}/favorites",
        f"{MINI}/orders",
    ):
        headers = _bearer(commerce_customers[0]) if path.startswith(MINI) else {}
        for query in ("page=0", "page=1000001", "page_size=0", "page_size=101"):
            _assert_chinese_error(admin_client.get(f"{path}?{query}", headers=headers), 422)
        response = admin_client.get(path, headers=headers)
        assert response.status_code == 200
        assert response.json() == {"data": []}


def test_mini_commerce_does_not_accept_browser_admin_without_bearer(
    admin_client: TestClient,
) -> None:
    for method, path in (
        ("GET", "favorites"),
        ("PUT", "favorites/PUBLIC-1"),
        ("DELETE", "favorites/PUBLIC-1"),
        ("GET", "favorites/PUBLIC-1"),
        ("GET", "coupons"),
        ("POST", "coupons/1/claim"),
        ("GET", "orders"),
    ):
        response = admin_client.request(method, f"{MINI}/{path}")
        _assert_chinese_error(response, 401)
        assert response.headers["cache-control"] == "private, no-store"


def test_admin_commerce_requires_admin_cookie_and_same_origin(
    client: TestClient,
    app: FastAPI,
    commerce_customers: list[dict[str, Any]],
) -> None:
    for method, path in (
        ("GET", "coupons"),
        ("POST", "coupons"),
        ("PATCH", "coupons/1"),
        ("GET", "orders"),
        ("POST", "orders"),
        ("POST", "orders/1/void"),
    ):
        response = client.request(method, f"{ADMIN}/{path}", headers=_bearer(commerce_customers[0]))
        _assert_chinese_error(response, 401)
    credentials = {"username": "commerce-admin", "password": "correct horse battery staple"}
    registered = client.post("/api/v1/auth/register", json=credentials).json()["data"]
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
    _assert_chinese_error(client.get(f"{ADMIN}/orders"), 403)
    from app.models import User

    with app.state.session_factory() as session:
        user = session.get(User, registered["id"])
        assert user is not None
        user.is_admin = True
        session.commit()
    response = client.post(
        f"{ADMIN}/coupons", headers={"Origin": "https://attacker.example"}, json={}
    )
    _assert_chinese_error(response, 403)
