from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.rate_limit import SlidingWindowRateLimiter
from app.models import (
    Category,
    MiniCustomer,
    ObjectCleanupJob,
    Product,
    ShopCoupon,
    ShopMedia,
    ShopProfile,
)
from app.services.object_cleanup import enqueue_object_cleanup, run_object_cleanup_jobs


@pytest.fixture
def shop_products(app: FastAPI) -> dict[str, int]:
    with app.state.session_factory() as session:
        active = Category(code="COFFEE", name="咖啡", is_active=True)
        inactive = Category(code="HIDDEN", name="隐藏", is_active=False)
        session.add_all([active, inactive])
        session.flush()
        products = [
            Product(
                product_code="APPLE-01",
                name="苹果美式",
                category_id=active.id,
                status="published",
                base_price_cents=1600,
                featured=True,
            ),
            Product(
                product_code="APPLE-02",
                name="苹果气泡咖啡",
                category_id=active.id,
                status="published",
                base_price_cents=1800,
            ),
            Product(
                product_code="DRAFT",
                name="苹果草稿",
                category_id=active.id,
                status="draft",
                base_price_cents=900,
            ),
            Product(
                product_code="HIDDEN",
                name="苹果隐藏",
                category_id=inactive.id,
                status="published",
                base_price_cents=800,
            ),
        ]
        session.add_all(products)
        session.commit()
        return {product.product_code: product.id for product in products} | {"category": active.id}


def _counts(app: FastAPI) -> dict[str, int]:
    with app.state.session_factory() as session:
        return dict(session.execute(select(Product.product_code, Product.search_hit_count)).all())


def test_empty_home_has_honest_defaults_and_does_not_create_store(client: TestClient, app: FastAPI):
    response = client.get("/api/v1/shop/home")
    assert response.status_code == 200, response.text
    home = response.json()["data"]
    assert set(home) == {
        "store",
        "carousel",
        "coupons",
        "categories",
        "hot_products",
        "hot_products_source",
    }
    assert home["hot_products_source"] == "newest"
    assert home["hot_products"] == home["carousel"] == home["coupons"] == home["categories"] == []
    assert home["store"]["announcement_image_url"] is None
    assert home["store"]["name"] == "港湾集市"
    assert "owner_customer_id" not in home["store"]
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ShopProfile)) == 0


def test_explicit_search_counts_all_matches_once_and_get_pagination_does_not_count(
    client: TestClient, app: FastAPI, shop_products: dict[str, int]
) -> None:
    response = client.post("/api/v1/shop/search", json={"q": " 苹果 ", "page_size": 1})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["total"] == 2 and len(data["items"]) == 1
    assert _counts(app) == {"APPLE-01": 1, "APPLE-02": 1, "DRAFT": 0, "HIDDEN": 0}
    assert client.get("/api/v1/catalog/products?q=苹果&page=2&page_size=1").status_code == 200
    assert _counts(app)["APPLE-01"] == 1
    assert client.post("/api/v1/shop/search", json={"q": "apple-01"}).json()["data"]["total"] == 1
    assert _counts(app)["APPLE-01"] == 2 and _counts(app)["APPLE-02"] == 1
    assert client.post("/api/v1/shop/search", json={"q": "没有此商品"}).json()["data"]["total"] == 0
    assert _counts(app)["APPLE-01"] == 2


@pytest.mark.parametrize(
    "body",
    [
        {"q": " "},
        {"q": "苹果\x00"},
        {"q": "x" * 161},
        {"q": "苹果", "page": 0},
        {"q": "苹果", "search_hit_count": 100},
    ],
)
def test_invalid_search_does_not_change_counters(
    client: TestClient, app: FastAPI, shop_products: dict[str, int], body: dict[str, Any]
) -> None:
    response = client.post("/api/v1/shop/search", json=body)
    assert response.status_code == 422
    assert "请求参数无效" in response.json()["error"]["message"]
    assert set(_counts(app).values()) == {0}


def test_search_rejects_invalid_unicode_before_database_query(
    client: TestClient, app: FastAPI, shop_products: dict[str, int]
) -> None:
    response = client.post(
        "/api/v1/shop/search",
        content=b'{"q":"\\ud800"}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert set(_counts(app).values()) == {0}


def test_hot_searches_rank_only_actual_public_hits(
    client: TestClient, app: FastAPI, shop_products: dict[str, int]
) -> None:
    assert client.get("/api/v1/shop/hot-searches").json() == {"data": []}
    with app.state.session_factory() as session:
        for code, count in {"APPLE-01": 2, "APPLE-02": 3, "DRAFT": 100, "HIDDEN": 200}.items():
            session.get(Product, shop_products[code]).search_hit_count = count
        session.commit()
    data = client.get("/api/v1/shop/hot-searches?limit=1").json()["data"]
    assert len(data) == 1 and data[0]["product"]["product_code"] == "APPLE-02"
    assert data[0]["search_hit_count"] == 3
    assert client.get("/api/v1/shop/hot-searches?limit=101").status_code == 422


def test_no_orders_uses_featured_then_newest_and_never_fakes_sales(
    client: TestClient, app: FastAPI, shop_products: dict[str, int]
) -> None:
    data = client.get("/api/v1/shop/home").json()["data"]
    assert data["hot_products_source"] == "featured"
    assert [item["product"]["product_code"] for item in data["hot_products"]] == ["APPLE-01"]
    assert data["hot_products"][0]["sold_quantity"] == 0
    with app.state.session_factory() as session:
        session.get(Product, shop_products["APPLE-01"]).featured = False
        session.commit()
    data = client.get("/api/v1/shop/home").json()["data"]
    assert data["hot_products_source"] == "newest"
    assert {item["product"]["product_code"] for item in data["hot_products"]} == {
        "APPLE-01",
        "APPLE-02",
    }
    assert all(
        item["order_count"] == item["repeat_purchase_count"] == 0 for item in data["hot_products"]
    )


def test_sales_ranking_aggregates_completed_histories_and_excludes_voided_orders(
    client: TestClient, admin_client: TestClient, app: FastAPI, shop_products: dict[str, int]
) -> None:
    with app.state.session_factory() as session:
        customer = MiniCustomer(app_id="test-app", openid="test-customer")
        session.add(customer)
        session.commit()
        customer_id = customer.id

    def record(reference: str, quantity: int, owner: int | None) -> int:
        response = admin_client.post(
            "/api/v1/admin/shop/orders",
            json={
                "external_reference": reference,
                "customer_id": owner,
                "items": [
                    {"product_code": "APPLE-01", "quantity": quantity, "unit_price_cents": 1500}
                ],
            },
        )
        assert response.status_code == 200, response.text
        return response.json()["data"]["id"]

    record("one", 2, customer_id)
    record("two", 3, customer_id)
    record("anonymous-one", 4, None)
    record("anonymous-two", 1, None)
    void_id = record("voided", 100, customer_id)
    assert admin_client.post(f"/api/v1/admin/shop/orders/{void_id}/void").status_code == 200
    response = client.get("/api/v1/shop/home")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["hot_products_source"] == "sales"
    assert len(data["hot_products"]) == 1
    product = data["hot_products"][0]
    assert (product["sold_quantity"], product["order_count"], product["repeat_purchase_count"]) == (
        10,
        4,
        1,
    )


def test_home_shows_only_current_coupons_and_active_category_images(
    client: TestClient, app: FastAPI, shop_products: dict[str, int], fake_object_storage: Any
) -> None:
    now = datetime.now(UTC)
    with app.state.session_factory() as session:
        session.add_all(
            [
                ShopCoupon(
                    title="有效券",
                    min_spend_cents=10000,
                    discount_cents=1000,
                    starts_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=1),
                ),
                ShopCoupon(
                    title="过期券",
                    min_spend_cents=10000,
                    discount_cents=1000,
                    starts_at=now - timedelta(days=2),
                    expires_at=now - timedelta(days=1),
                ),
                ShopCoupon(
                    title="未开始",
                    min_spend_cents=10000,
                    discount_cents=1000,
                    starts_at=now + timedelta(days=1),
                    expires_at=now + timedelta(days=2),
                ),
            ]
        )
        media = ShopMedia(
            kind="category",
            category_id=shop_products["category"],
            title="咖啡图片",
            media_type="image",
            object_key="shop/category.jpg",
            mime_type="image/jpeg",
            size_bytes=3,
            sha256=hashlib.sha256(b"abc").hexdigest(),
        )
        session.add(media)
        session.commit()
        media_id = media.id
    data = client.get("/api/v1/shop/home").json()["data"]
    assert [coupon["title"] for coupon in data["coupons"]] == ["有效券"]
    assert data["categories"] == [
        {
            "id": shop_products["category"],
            "code": "COFFEE",
            "name": "咖啡",
            "image_url": f"/api/v1/shop/media/{media_id}",
        }
    ]


def test_cleanup_keeps_referenced_shop_media(app: FastAPI, fake_object_storage: Any) -> None:
    payload = b"abc"
    key = "shop/announcement.jpg"
    fake_object_storage.put(key, payload, content_type="image/jpeg")
    with app.state.session_factory() as session:
        media = ShopMedia(
            kind="announcement",
            media_type="image",
            object_key=key,
            mime_type="image/jpeg",
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        session.add(media)
        job = enqueue_object_cleanup(session, [key], reason="test_stale_intent")[0]
        session.commit()
        assert run_object_cleanup_jobs(session, fake_object_storage, [job]) == []
        assert key in fake_object_storage.objects
        assert session.get(ObjectCleanupJob, job.id).status == "completed"


def test_customer_selection_requires_admin_and_does_not_expose_provider_identity(
    client: TestClient, admin_client: TestClient, app: FastAPI
) -> None:
    with TestClient(app) as guest:
        response = guest.get("/api/v1/admin/shop/customers")
    assert response.status_code == 401 and response.json()["error"]["message"] == "请先登录"
    with app.state.session_factory() as session:
        session.add(MiniCustomer(app_id="private-app", openid="private-openid", nickname="店主"))
        session.commit()
    response = admin_client.get("/api/v1/admin/shop/customers")
    assert response.status_code == 200, response.text
    assert set(response.json()["data"][0]) == {"id", "nickname"}
    assert "private-openid" not in response.text and "private-app" not in response.text


def test_category_deletion_preserves_durable_media_cleanup(
    admin_client: TestClient, app: FastAPI, fake_object_storage: Any
) -> None:
    key = "shop/category/deleted.jpg"
    payload = b"category-image"
    fake_object_storage.put(key, payload, content_type="image/jpeg")
    with app.state.session_factory() as session:
        category = Category(code="EMPTY", name="空分类")
        session.add(category)
        session.flush()
        category_id = category.id
        session.add(
            ShopMedia(
                kind="category",
                category_id=category_id,
                media_type="image",
                object_key=key,
                mime_type="image/jpeg",
                size_bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
            )
        )
        session.commit()
    assert admin_client.delete(f"/api/v1/admin/categories/{category_id}").status_code == 204
    with app.state.session_factory() as session:
        assert session.scalar(select(ShopMedia.id).where(ShopMedia.object_key == key)) is None
        job = session.scalar(select(ObjectCleanupJob).where(ObjectCleanupJob.object_key == key))
        assert job is not None and job.status == "pending"
        assert run_object_cleanup_jobs(session, fake_object_storage, [job]) == []
        assert key not in fake_object_storage.objects


def test_public_search_rate_limit_blocks_forged_ip_and_does_not_add_extra_hits(
    client: TestClient, app: FastAPI, shop_products: dict[str, int]
) -> None:
    app.state.shop_search_rate_limiter = SlidingWindowRateLimiter(1, 60, max_keys=100)
    first = client.post(
        "/api/v1/shop/search", json={"q": "苹果"}, headers={"X-Real-IP": "192.0.2.1"}
    )
    assert first.status_code == 200
    second = client.post(
        "/api/v1/shop/search", json={"q": "苹果"}, headers={"X-Real-IP": "192.0.2.2"}
    )
    assert second.status_code == 429
    assert 1 <= int(second.headers["Retry-After"]) <= 60
    assert second.json()["error"]["code"] == "shop_search_rate_limited"
    assert _counts(app)["APPLE-01"] == _counts(app)["APPLE-02"] == 1


def test_malformed_search_json_consumes_limit_before_parsing(
    client: TestClient, app: FastAPI
) -> None:
    app.state.shop_search_rate_limiter = SlidingWindowRateLimiter(1, 60, max_keys=100)
    malformed = client.post(
        "/api/v1/shop/search", content=b"{", headers={"Content-Type": "application/json"}
    )
    assert malformed.status_code == 422
    assert client.post("/api/v1/shop/search", json={"q": "苹果"}).status_code == 429
