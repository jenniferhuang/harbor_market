from __future__ import annotations

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Category, ObjectCleanupJob


def _image_bytes(image_format: str = "PNG", size: tuple[int, int] = (3, 2)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, color=(31, 92, 77)).save(output, format=image_format)
    return output.getvalue()


def _create_category(
    client: TestClient,
    *,
    code: str = "COFFEE",
    name: str = "咖啡",
    is_active: bool = True,
    parent_id: int | None = None,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/admin/categories",
        json={
            "code": code,
            "name": name,
            "description": f"{name}商品",
            "is_active": is_active,
            "parent_id": parent_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _create_product(
    client: TestClient,
    category_id: int,
    *,
    code: str = "LATTE-01",
    name: str = "生椰拿铁",
    status: str = "draft",
    skus: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_code": code,
        "name": name,
        "subtitle": "现磨咖啡",
        "category_id": category_id,
        "status": status,
        "base_price_cents": 1990,
        "market_price_cents": 2390,
        "inventory_count": 17,
        "tags": ["新品", "咖啡"],
        "selling_points": ["现磨", "低糖可选"],
        "description": "浓郁顺滑",
        "ingredients": "咖啡、椰乳",
        "allergen_info": "含椰制品",
        "specifications": [{"code": "temperature", "name": "温度"}],
    }
    if skus is not None:
        payload["skus"] = skus
    response = client.post("/api/v1/admin/products", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _upload_image(
    client: TestClient,
    product_id: int,
    *,
    image_type: str,
    payload: bytes | None = None,
    filename: str = "product.png",
    sort_order: int = 0,
) -> Any:
    return client.post(
        f"/api/v1/admin/products/{product_id}/images",
        files={"file": (filename, _image_bytes() if payload is None else payload, "image/png")},
        data={"image_type": image_type, "alt_text": "商品图片", "sort_order": str(sort_order)},
    )


def _login_catalog_admin(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "catalog-admin",
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/catalog/products?page=1000001&page_size=100",
        "/api/v1/admin/products?page=1000001&page_size=100",
        "/api/v1/catalog/products?page=999999999999999999&page_size=100",
    ],
)
def test_catalog_page_is_bounded(path: str, admin_client: TestClient) -> None:
    response = admin_client.get(path)

    assert response.status_code == 422


def test_every_admin_route_requires_login_then_admin_permission(
    client: TestClient,
) -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", "/api/v1/admin/categories", {}),
        ("POST", "/api/v1/admin/categories", {"json": {}}),
        ("PATCH", "/api/v1/admin/categories/1", {"json": {}}),
        ("DELETE", "/api/v1/admin/categories/1", {}),
        ("GET", "/api/v1/admin/products", {}),
        ("POST", "/api/v1/admin/products", {"json": {}}),
        ("GET", "/api/v1/admin/products/template.xlsx", {}),
        ("GET", "/api/v1/admin/products/export.xlsx", {}),
        (
            "POST",
            "/api/v1/admin/products/import?dry_run=true",
            {"files": {"file": ("products.xlsx", b"not-a-workbook")}},
        ),
        ("GET", "/api/v1/admin/import-jobs", {}),
        ("GET", "/api/v1/admin/import-jobs/1", {}),
        (
            "POST",
            "/api/v1/admin/product-images/staging",
            {
                "files": {"file": ("image.png", _image_bytes(), "image/png")},
                "data": {"product_code": "AUTH-CHECK"},
            },
        ),
        (
            "DELETE",
            "/api/v1/admin/product-images/staging/products/staged/AUTH-CHECK/"
            "0123456789abcdef0123456789abcdef.png",
            {},
        ),
        ("GET", "/api/v1/admin/object-cleanup-jobs", {}),
        ("POST", "/api/v1/admin/object-cleanup-jobs/1/retry", {}),
        ("GET", "/api/v1/admin/products/1", {}),
        ("PATCH", "/api/v1/admin/products/1", {"json": {}}),
        ("DELETE", "/api/v1/admin/products/1", {}),
        (
            "POST",
            "/api/v1/admin/products/1/images",
            {"files": {"file": ("image.png", _image_bytes())}},
        ),
        ("PATCH", "/api/v1/admin/products/1/images/1", {"json": {}}),
        ("DELETE", "/api/v1/admin/products/1/images/1", {}),
    ]

    for method, path, kwargs in requests:
        response = client.request(method, path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["error"]["code"] == "authentication_required"
        assert response.headers["cache-control"] == "private, no-store"

    credentials = {"username": "ordinary-user", "password": "correct horse battery staple"}
    registered = client.post("/api/v1/auth/register", json=credentials)
    assert registered.status_code == 201
    assert registered.json()["data"]["is_admin"] is False
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200

    for method, path, kwargs in requests:
        response = client.request(method, path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["code"] == "admin_required"


def test_admin_unsafe_requests_reject_cross_origin_browser_forms(
    admin_client: TestClient,
) -> None:
    hostile = admin_client.post(
        "/api/v1/admin/categories",
        headers={"Origin": "https://attacker.example"},
        json={"code": "CSRF", "name": "不应创建"},
    )
    assert hostile.status_code == 403
    assert hostile.json()["error"]["code"] == "csrf_origin_mismatch"

    trusted = admin_client.post(
        "/api/v1/admin/categories",
        headers={"Origin": "http://testserver"},
        json={"code": "TRUSTED", "name": "同源请求"},
    )
    assert trusted.status_code == 201
    assert trusted.headers["cache-control"] == "private, no-store"

    proxied_port = admin_client.post(
        "/api/v1/admin/categories",
        headers={
            "Host": "testserver:8080",
            "X-Forwarded-Host": "testserver:8080",
            "X-Forwarded-Proto": "http",
            "Origin": "http://testserver:8080",
        },
        json={"code": "TRUSTED-PORT", "name": "同源端口"},
    )
    assert proxied_port.status_code == 201


def test_category_crud_parent_rules_and_public_active_filter(
    admin_client: TestClient,
) -> None:
    parent = _create_category(admin_client, code="DRINK", name="饮品")
    child = _create_category(
        admin_client,
        code="coffee",
        name="咖啡",
        parent_id=parent["id"],
    )
    assert child["code"] == "COFFEE"

    updated = admin_client.patch(
        f"/api/v1/admin/categories/{child['id']}",
        json={"name": "咖啡饮品", "sort_order": 8, "is_active": False},
    )
    assert updated.status_code == 200
    assert {
        "name": "咖啡饮品",
        "sort_order": 8,
        "is_active": False,
    }.items() <= updated.json()["data"].items()

    admin_codes = {
        item["code"] for item in admin_client.get("/api/v1/admin/categories").json()["data"]
    }
    public_codes = {
        item["code"] for item in admin_client.get("/api/v1/catalog/categories").json()["data"]
    }
    assert admin_codes == {"DRINK", "COFFEE"}
    assert public_codes == {"DRINK"}

    self_parent = admin_client.patch(
        f"/api/v1/admin/categories/{parent['id']}",
        json={"parent_id": parent["id"]},
    )
    assert self_parent.status_code == 422
    assert self_parent.json()["error"]["code"] == "invalid_category_parent"

    cycle = admin_client.patch(
        f"/api/v1/admin/categories/{parent['id']}",
        json={"parent_id": child["id"]},
    )
    assert cycle.status_code == 422
    assert cycle.json()["error"]["code"] == "category_cycle"

    in_use = admin_client.delete(f"/api/v1/admin/categories/{parent['id']}")
    assert in_use.status_code == 409
    assert in_use.json()["error"]["code"] == "category_in_use"
    assert admin_client.delete(f"/api/v1/admin/categories/{child['id']}").status_code == 204
    assert admin_client.delete(f"/api/v1/admin/categories/{parent['id']}").status_code == 204
    assert admin_client.get("/api/v1/admin/categories").json()["data"] == []


def test_category_delete_translates_late_foreign_key_conflict(
    admin_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    category = _create_category(admin_client, code="DELETE-RACE", name="删除竞态")
    original_commit = Session.commit
    original_rollback = Session.rollback
    flushed_category_ids: list[int] = []
    rollback_states: list[tuple[bool, bool]] = []

    def fail_late_category_delete(session: Session) -> None:
        deleted_categories = [
            item for item in session.deleted if isinstance(item, Category)
        ]
        if deleted_categories:
            flushed_category_ids.extend(item.id for item in deleted_categories)
            session.flush()
            raise IntegrityError("DELETE FROM categories", {}, RuntimeError("foreign key"))
        original_commit(session)

    def record_rollback(session: Session) -> None:
        had_transaction = session.in_transaction()
        original_rollback(session)
        rollback_states.append((had_transaction, session.in_transaction()))

    monkeypatch.setattr(Session, "commit", fail_late_category_delete)
    monkeypatch.setattr(Session, "rollback", record_rollback)

    response = admin_client.delete(f"/api/v1/admin/categories/{category['id']}")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "category_in_use"
    assert flushed_category_ids == [category["id"]]
    assert rollback_states == [(True, False)]
    assert {
        item["code"] for item in admin_client.get("/api/v1/admin/categories").json()["data"]
    } == {"DELETE-RACE"}


def test_product_crud_builds_default_sku_and_replaces_sku_set(
    admin_client: TestClient,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"], code="latte-01")

    assert product["product_code"] == "LATTE-01"
    assert product["status"] == "draft"
    assert len(product["skus"]) == 1
    assert {
        "sku_code": "LATTE-01-DEFAULT",
        "name": "默认规格",
        "price_cents": 1990,
        "stock_quantity": 17,
        "is_default": True,
        "is_active": True,
    }.items() <= product["skus"][0].items()

    replacement_skus = [
        {
            "sku_code": "LATTE-LARGE",
            "name": "大杯",
            "price_cents": 2390,
            "stock_quantity": 5,
            "attributes": {"cup": "large"},
            "is_default": False,
        },
        {
            "sku_code": "LATTE-SMALL",
            "name": "小杯",
            "price_cents": 1990,
            "stock_quantity": 7,
            "attributes": {"cup": "small"},
            "is_default": False,
        },
    ]
    updated = admin_client.patch(
        f"/api/v1/admin/products/{product['id']}",
        json={"name": "升级生椰拿铁", "featured": True, "skus": replacement_skus},
    )
    assert updated.status_code == 200, updated.text
    updated_product = updated.json()["data"]
    assert updated_product["name"] == "升级生椰拿铁"
    assert [sku["sku_code"] for sku in updated_product["skus"]] == [
        "LATTE-LARGE",
        "LATTE-SMALL",
    ]
    assert [sku["is_default"] for sku in updated_product["skus"]] == [True, False]

    listing = admin_client.get(
        "/api/v1/admin/products",
        params={"q": "升级生椰", "status": "draft", "category_id": category["id"]},
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["total"] == 1
    assert listing.json()["data"]["items"][0]["id"] == product["id"]

    category_in_use = admin_client.delete(f"/api/v1/admin/categories/{category['id']}")
    assert category_in_use.status_code == 409
    assert admin_client.delete(f"/api/v1/admin/products/{product['id']}").status_code == 204
    assert admin_client.get(f"/api/v1/admin/products/{product['id']}").status_code == 404
    assert admin_client.delete(f"/api/v1/admin/categories/{category['id']}").status_code == 204


@pytest.mark.parametrize("initial_default", ["SKU-SWAP-A", "SKU-SWAP-B"])
def test_default_sku_can_swap_in_either_database_id_direction(
    admin_client: TestClient,
    initial_default: str,
) -> None:
    category = _create_category(admin_client)
    target_default = "SKU-SWAP-B" if initial_default == "SKU-SWAP-A" else "SKU-SWAP-A"
    created = _create_product(
        admin_client,
        category["id"],
        code="SKU-SWAP",
        skus=[
            {
                "sku_code": "SKU-SWAP-A",
                "name": "规格 A",
                "price_cents": 1900,
                "is_default": initial_default == "SKU-SWAP-A",
                "is_active": True,
                "sort_order": 1,
            },
            {
                "sku_code": "SKU-SWAP-B",
                "name": "规格 B",
                "price_cents": 2100,
                "is_default": initial_default == "SKU-SWAP-B",
                "is_active": True,
                "sort_order": 2,
            },
        ],
    )
    original_ids = {sku["sku_code"]: sku["id"] for sku in created["skus"]}

    swapped = admin_client.patch(
        f"/api/v1/admin/products/{created['id']}",
        json={
            "skus": [
                {
                    "sku_code": code,
                    "name": f"规格 {code[-1]}",
                    "price_cents": 1900 if code.endswith("A") else 2100,
                    "is_default": code == target_default,
                    "is_active": True,
                    "sort_order": 1 if code.endswith("A") else 2,
                }
                for code in ("SKU-SWAP-A", "SKU-SWAP-B")
            ]
        },
    )
    assert swapped.status_code == 200, swapped.text
    result = swapped.json()["data"]["skus"]
    assert {sku["sku_code"]: sku["id"] for sku in result} == original_ids
    assert [sku["sku_code"] for sku in result if sku["is_default"]] == [target_default]


def test_publish_requires_cover_and_public_catalog_filters_status_and_category(
    admin_client: TestClient,
) -> None:
    active_category = _create_category(admin_client, code="COFFEE", name="咖啡")
    inactive_category = _create_category(
        admin_client,
        code="HIDDEN",
        name="隐藏类目",
        is_active=False,
    )
    draft = _create_product(admin_client, active_category["id"], code="DRAFT-01")
    archived = _create_product(
        admin_client,
        active_category["id"],
        code="ARCHIVED-01",
        status="archived",
    )
    public_product = _create_product(
        admin_client,
        active_category["id"],
        code="PUBLIC-01",
        name="公开拿铁",
    )

    missing_cover = admin_client.patch(
        f"/api/v1/admin/products/{public_product['id']}",
        json={"status": "published"},
    )
    assert missing_cover.status_code == 422
    assert missing_cover.json()["error"]["code"] == "cover_required"
    assert (
        admin_client.get(f"/api/v1/admin/products/{public_product['id']}").json()["data"]["status"]
        == "draft"
    )

    cover_upload = _upload_image(admin_client, public_product["id"], image_type="cover")
    assert cover_upload.status_code == 200, cover_upload.text
    cover = cover_upload.json()["data"]["images"][0]
    published = admin_client.patch(
        f"/api/v1/admin/products/{public_product['id']}",
        json={"status": "published"},
    )
    assert published.status_code == 200, published.text

    inactive_product = _create_product(
        admin_client,
        inactive_category["id"],
        code="INACTIVE-01",
    )
    inactive_cover = _upload_image(admin_client, inactive_product["id"], image_type="cover")
    assert inactive_cover.status_code == 200
    inactive_publish = admin_client.patch(
        f"/api/v1/admin/products/{inactive_product['id']}",
        json={"status": "published"},
    )
    assert inactive_publish.status_code == 422
    assert inactive_publish.json()["error"]["code"] == "inactive_category"

    public_listing = admin_client.get(
        "/api/v1/catalog/products",
        params={"q": "公开", "category": "coffee"},
    )
    assert public_listing.status_code == 200
    assert public_listing.json()["data"]["total"] == 1
    assert public_listing.json()["data"]["items"][0]["product_code"] == "PUBLIC-01"
    assert admin_client.get("/api/v1/catalog/products/PUBLIC-01").status_code == 200
    assert admin_client.get(f"/api/v1/catalog/products/{draft['product_code']}").status_code == 404
    assert (
        admin_client.get(f"/api/v1/catalog/products/{archived['product_code']}").status_code == 404
    )
    assert (
        admin_client.get(f"/api/v1/catalog/products/{inactive_product['product_code']}").status_code
        == 404
    )
    public_categories = admin_client.get("/api/v1/catalog/categories").json()["data"]
    assert {item["code"] for item in public_categories} == {"COFFEE"}

    protected_cover = admin_client.delete(
        f"/api/v1/admin/products/{public_product['id']}/images/{cover['id']}"
    )
    assert protected_cover.status_code == 409
    assert protected_cover.json()["error"]["code"] == "published_cover"
    protected_product = admin_client.delete(f"/api/v1/admin/products/{public_product['id']}")
    assert protected_product.status_code == 409
    assert protected_product.json()["error"]["code"] == "published_product"

    deactivated = admin_client.patch(
        f"/api/v1/admin/categories/{active_category['id']}",
        json={"is_active": False},
    )
    assert deactivated.status_code == 200
    assert admin_client.get("/api/v1/catalog/products").json()["data"]["total"] == 0
    assert admin_client.get("/api/v1/catalog/products/PUBLIC-01").status_code == 404


def test_image_upload_media_read_metadata_update_and_delete(
    admin_client: TestClient,
    fake_object_storage: Any,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"])
    payload = _image_bytes("WEBP", (7, 5))

    uploaded = _upload_image(
        admin_client,
        product["id"],
        image_type="gallery",
        payload=payload,
        filename="client-name.jpg",
        sort_order=4,
    )
    assert uploaded.status_code == 200, uploaded.text
    image = uploaded.json()["data"]["images"][0]
    assert {
        "image_type": "gallery",
        "alt_text": "商品图片",
        "sort_order": 4,
        "mime_type": "image/webp",
        "size_bytes": len(payload),
        "width": 7,
        "height": 5,
    }.items() <= image.items()
    assert re.fullmatch(
        rf"products/{product['id']}/gallery/[0-9a-f]{{32}}\.webp",
        image["object_key"],
    )
    assert fake_object_storage.objects[image["object_key"]] == payload
    assert "sha256" in fake_object_storage.stats[image["object_key"]].metadata

    media = admin_client.get(image["url"])
    assert media.status_code == 200
    assert media.content == payload
    assert media.headers["content-type"] == "image/webp"
    assert media.headers["cache-control"] == "private, no-store"

    updated = admin_client.patch(
        f"/api/v1/admin/products/{product['id']}/images/{image['id']}",
        json={"alt_text": "新替代文本", "sort_order": 1},
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["images"][0]["alt_text"] == "新替代文本"
    assert updated.json()["data"]["images"][0]["sort_order"] == 1

    deleted = admin_client.delete(f"/api/v1/admin/products/{product['id']}/images/{image['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["data"]["images"] == []
    assert fake_object_storage.delete_calls == [image["object_key"]]
    assert image["object_key"] not in fake_object_storage.objects
    assert admin_client.get(image["url"]).status_code == 404


def test_staging_upload_validates_image_and_generates_normalized_object_key(
    admin_client: TestClient,
    fake_object_storage: Any,
    app: FastAPI,
) -> None:
    payload = _image_bytes("WEBP", (11, 6))
    uploaded = admin_client.post(
        "/api/v1/admin/product-images/staging",
        files={"file": ("misleading-name.jpg", payload, "image/jpeg")},
        data={"product_code": " stage-01 "},
    )
    assert uploaded.status_code == 200, uploaded.text
    staged = uploaded.json()["data"]
    assert {
        "mime_type": "image/webp",
        "size_bytes": len(payload),
        "width": 11,
        "height": 6,
    }.items() <= staged.items()
    assert re.fullmatch(
        r"products/staged/STAGE-01/[0-9a-f]{32}\.webp",
        staged["object_key"],
    )
    assert fake_object_storage.objects[staged["object_key"]] == payload
    assert "sha256" in fake_object_storage.stats[staged["object_key"]].metadata
    expires_at = datetime.fromisoformat(staged["expires_at"])
    assert datetime.now(UTC) + timedelta(days=6, hours=23) < expires_at
    cleanup_jobs = admin_client.get(
        "/api/v1/admin/object-cleanup-jobs", params={"status": "pending"}
    ).json()["data"]
    expiry_job = next(item for item in cleanup_jobs if item["reason"] == "staging_expiry")
    assert expiry_job["object_key"] == staged["object_key"]
    assert expiry_job["created_by"] is not None
    assert expiry_job["not_before"] is not None

    invalid_cases = [
        (b"", "empty.png", "invalid_image"),
        (b"plain text", "fake.png", "invalid_image"),
        (_image_bytes("GIF"), "image.gif", "unsupported_image"),
    ]
    for invalid_payload, filename, error_code in invalid_cases:
        response = admin_client.post(
            "/api/v1/admin/product-images/staging",
            files={"file": (filename, invalid_payload, "application/octet-stream")},
            data={"product_code": "STAGE-01"},
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == error_code

    valid_png = _image_bytes()
    app.state.settings.upload_max_bytes = len(valid_png) - 1
    oversized = admin_client.post(
        "/api/v1/admin/product-images/staging",
        files={"file": ("oversized.png", valid_png, "image/png")},
        data={"product_code": "STAGE-01"},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "image_too_large"

    cancelled = admin_client.delete(f"/api/v1/admin/product-images/staging/{staged['object_key']}")
    assert cancelled.status_code == 204
    assert staged["object_key"] not in fake_object_storage.objects
    all_jobs = admin_client.get("/api/v1/admin/object-cleanup-jobs").json()["data"]
    expiry_job = next(item for item in all_jobs if item["reason"] == "staging_expiry")
    assert expiry_job["status"] == "completed"


def test_staging_quota_escapes_underscore_product_codes(
    admin_client: TestClient,
    fake_object_storage: Any,
    app: FastAPI,
) -> None:
    future = datetime.now(UTC) + timedelta(days=7)
    with app.state.session_factory() as session:
        session.add_all(
            [
                ObjectCleanupJob(
                    object_key=f"products/staged/A1B/{index:032x}.png",
                    reason="staging_expiry",
                    status="pending",
                    not_before=future,
                )
                for index in range(100)
            ]
            + [
                ObjectCleanupJob(
                    object_key=f"products/staged/A_B/{index + 1000:032x}.png",
                    reason="staging_expiry",
                    status="pending",
                    not_before=future,
                )
                for index in range(99)
            ]
        )
        session.commit()

    payload = _image_bytes()
    accepted = admin_client.post(
        "/api/v1/admin/product-images/staging",
        files={"file": ("image.png", payload, "image/png")},
        data={"product_code": "A_B"},
    )
    assert accepted.status_code == 200, accepted.text
    put_count = len(fake_object_storage.put_calls)

    rejected = admin_client.post(
        "/api/v1/admin/product-images/staging",
        files={"file": ("image.png", payload, "image/png")},
        data={"product_code": "A_B"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "staging_quota_exceeded"
    assert len(fake_object_storage.put_calls) == put_count


def test_media_distinguishes_missing_transport_and_integrity_failures(
    admin_client: TestClient,
    fake_object_storage: Any,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"], code="MEDIA-FAILURES")
    payload = _image_bytes("WEBP", (7, 5))
    uploaded = _upload_image(
        admin_client,
        product["id"],
        image_type="gallery",
        payload=payload,
        filename="product.webp",
    )
    assert uploaded.status_code == 200
    image = uploaded.json()["data"]["images"][0]
    object_key = image["object_key"]
    original_stat = fake_object_storage.stats[object_key]

    fake_object_storage.stats[object_key] = replace(
        original_stat,
        content_type="application/octet-stream",
    )
    media = admin_client.get(image["url"])
    assert media.status_code == 200
    assert media.content == payload
    assert media.headers["content-type"] == "image/webp"
    assert media.headers["content-security-policy"] == "default-src 'none'; sandbox"
    assert media.headers["x-content-type-options"] == "nosniff"

    fake_object_storage.stats[object_key] = replace(original_stat, size=len(payload) + 1)
    mismatched = admin_client.get(image["url"])
    assert mismatched.status_code == 503
    assert mismatched.json()["error"]["code"] == "media_integrity_error"

    fake_object_storage.stats[object_key] = original_stat
    fake_object_storage.fail_stat = True
    unavailable = admin_client.get(image["url"])
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "storage_unavailable"

    fake_object_storage.fail_stat = False
    del fake_object_storage.objects[object_key]
    del fake_object_storage.stats[object_key]
    missing = admin_client.get(image["url"])
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "media_not_found"


def test_deleting_draft_product_removes_all_attached_objects(
    admin_client: TestClient,
    fake_object_storage: Any,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"])
    gallery = _upload_image(admin_client, product["id"], image_type="gallery")
    detail = _upload_image(admin_client, product["id"], image_type="detail")
    assert gallery.status_code == detail.status_code == 200
    object_keys = {
        gallery.json()["data"]["images"][0]["object_key"],
        next(
            image["object_key"]
            for image in detail.json()["data"]["images"]
            if image["image_type"] == "detail"
        ),
    }

    deleted = admin_client.delete(f"/api/v1/admin/products/{product['id']}")
    assert deleted.status_code == 204
    assert set(fake_object_storage.delete_calls) == object_keys
    assert not object_keys & fake_object_storage.objects.keys()
    assert admin_client.get(f"/api/v1/admin/products/{product['id']}").status_code == 404


def test_image_delete_failure_is_retryable_after_database_reference_is_removed(
    admin_client: TestClient,
    fake_object_storage: Any,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"])

    fake_object_storage.fail_put = True
    failed_upload = _upload_image(admin_client, product["id"], image_type="gallery")
    assert failed_upload.status_code == 503
    assert failed_upload.json()["error"]["code"] == "storage_unavailable"
    current_product = admin_client.get(f"/api/v1/admin/products/{product['id']}")
    assert current_product.json()["data"]["images"] == []

    fake_object_storage.fail_put = False
    uploaded = _upload_image(admin_client, product["id"], image_type="gallery")
    assert uploaded.status_code == 200
    image = uploaded.json()["data"]["images"][0]
    fake_object_storage.fail_delete = True
    failed_delete = admin_client.delete(
        f"/api/v1/admin/products/{product['id']}/images/{image['id']}"
    )
    assert failed_delete.status_code == 503
    assert failed_delete.json()["error"]["code"] == "cleanup_pending"
    current = admin_client.get(f"/api/v1/admin/products/{product['id']}").json()["data"]
    assert current["images"] == []
    assert image["object_key"] in fake_object_storage.objects
    assert admin_client.get(image["url"]).status_code == 404

    failed_jobs = admin_client.get("/api/v1/admin/object-cleanup-jobs", params={"status": "failed"})
    assert failed_jobs.status_code == 200
    assert len(failed_jobs.json()["data"]) == 1
    job = failed_jobs.json()["data"][0]
    assert {
        "object_key": image["object_key"],
        "reason": "image_deleted",
        "status": "failed",
        "attempts": 1,
    }.items() <= job.items()
    assert "fake object storage delete failure" in job["last_error"]

    fake_object_storage.fail_delete = False
    retried = admin_client.post(f"/api/v1/admin/object-cleanup-jobs/{job['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert {
        "id": job["id"],
        "status": "completed",
        "attempts": 2,
        "last_error": None,
    }.items() <= retried.json()["data"].items()
    assert image["object_key"] not in fake_object_storage.objects


def test_product_delete_failure_is_retryable_after_database_product_is_removed(
    admin_client: TestClient,
    fake_object_storage: Any,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"], code="DELETE-RETRY")
    uploaded = _upload_image(admin_client, product["id"], image_type="gallery")
    assert uploaded.status_code == 200
    object_key = uploaded.json()["data"]["images"][0]["object_key"]

    fake_object_storage.fail_delete = True
    deleted = admin_client.delete(f"/api/v1/admin/products/{product['id']}")
    assert deleted.status_code == 503
    assert deleted.json()["error"]["code"] == "cleanup_pending"
    assert admin_client.get(f"/api/v1/admin/products/{product['id']}").status_code == 404
    assert object_key in fake_object_storage.objects

    jobs = admin_client.get(
        "/api/v1/admin/object-cleanup-jobs", params={"status": "failed"}
    ).json()["data"]
    assert len(jobs) == 1
    job = jobs[0]
    assert {
        "object_key": object_key,
        "reason": "product_deleted",
        "status": "failed",
        "attempts": 1,
    }.items() <= job.items()

    fake_object_storage.fail_delete = False
    retried = admin_client.post(f"/api/v1/admin/object-cleanup-jobs/{job['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["data"]["status"] == "completed"
    assert retried.json()["data"]["attempts"] == 2
    assert object_key not in fake_object_storage.objects


def test_draft_media_is_private_for_admin_and_hidden_from_anonymous_users(
    admin_client: TestClient,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"], code="PRIVATE-MEDIA")
    uploaded = _upload_image(admin_client, product["id"], image_type="gallery")
    assert uploaded.status_code == 200
    media_url = uploaded.json()["data"]["images"][0]["url"]

    admin_media = admin_client.get(media_url)
    assert admin_media.status_code == 200
    assert admin_media.headers["cache-control"] == "private, no-store"

    admin_client.cookies.clear()
    anonymous_media = admin_client.get(media_url)
    assert anonymous_media.status_code == 404
    assert anonymous_media.json()["error"]["code"] == "media_not_found"


def test_disabling_category_revokes_anonymous_media_but_keeps_private_admin_preview(
    admin_client: TestClient,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"], code="REVOKED-MEDIA")
    uploaded = _upload_image(admin_client, product["id"], image_type="cover")
    assert uploaded.status_code == 200
    media_url = uploaded.json()["data"]["images"][0]["url"]
    published = admin_client.patch(
        f"/api/v1/admin/products/{product['id']}",
        json={"status": "published"},
    )
    assert published.status_code == 200

    admin_client.cookies.clear()
    public_media = admin_client.get(media_url)
    assert public_media.status_code == 200
    assert public_media.headers["cache-control"] == "public, max-age=0, must-revalidate"

    _login_catalog_admin(admin_client)
    disabled = admin_client.patch(
        f"/api/v1/admin/categories/{category['id']}",
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    admin_preview = admin_client.get(media_url)
    assert admin_preview.status_code == 200
    assert admin_preview.headers["cache-control"] == "private, no-store"

    admin_client.cookies.clear()
    revoked = admin_client.get(media_url)
    assert revoked.status_code == 404
    assert revoked.json()["error"]["code"] == "media_not_found"


def test_public_product_responses_hide_inactive_skus(
    admin_client: TestClient,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(
        admin_client,
        category["id"],
        code="SKU-VISIBILITY",
        skus=[
            {
                "sku_code": "SKU-VISIBILITY-ACTIVE",
                "name": "在售规格",
                "price_cents": 1990,
                "is_default": True,
                "is_active": True,
            },
            {
                "sku_code": "SKU-VISIBILITY-INACTIVE",
                "name": "停售规格",
                "price_cents": 2090,
                "is_default": False,
                "is_active": False,
            },
        ],
    )
    cover = _upload_image(admin_client, product["id"], image_type="cover")
    assert cover.status_code == 200
    published = admin_client.patch(
        f"/api/v1/admin/products/{product['id']}",
        json={"status": "published"},
    )
    assert published.status_code == 200
    assert {sku["sku_code"] for sku in published.json()["data"]["skus"]} == {
        "SKU-VISIBILITY-ACTIVE",
        "SKU-VISIBILITY-INACTIVE",
    }

    public_detail = admin_client.get("/api/v1/catalog/products/SKU-VISIBILITY")
    assert public_detail.status_code == 200
    assert [sku["sku_code"] for sku in public_detail.json()["data"]["skus"]] == [
        "SKU-VISIBILITY-ACTIVE"
    ]
    public_listing = admin_client.get("/api/v1/catalog/products")
    assert public_listing.status_code == 200
    assert [sku["sku_code"] for sku in public_listing.json()["data"]["items"][0]["skus"]] == [
        "SKU-VISIBILITY-ACTIVE"
    ]


def test_rejects_empty_corrupt_unsupported_and_oversized_images(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"])
    gif_payload = _image_bytes("GIF")

    invalid_cases = [
        (b"", "empty.png", "invalid_image"),
        (b"plain text is not an image", "fake.png", "invalid_image"),
        (gif_payload, "image.gif", "unsupported_image"),
        (_image_bytes("PNG", (12_001, 1)), "wide.png", "invalid_image_dimensions"),
    ]
    for payload, filename, error_code in invalid_cases:
        response = _upload_image(
            admin_client,
            product["id"],
            image_type="gallery",
            payload=payload,
            filename=filename,
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == error_code

    valid_png = _image_bytes()
    app.state.settings.upload_max_bytes = len(valid_png) - 1
    oversized = _upload_image(
        admin_client,
        product["id"],
        image_type="gallery",
        payload=valid_png,
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "image_too_large"


@pytest.mark.parametrize(("image_type", "limit"), [("cover", 1), ("gallery", 8), ("detail", 20)])
def test_enforces_per_role_image_limits(
    admin_client: TestClient,
    fake_object_storage: Any,
    image_type: str,
    limit: int,
) -> None:
    category = _create_category(admin_client)
    product = _create_product(admin_client, category["id"])

    for sort_order in range(limit):
        response = _upload_image(
            admin_client,
            product["id"],
            image_type=image_type,
            sort_order=sort_order,
        )
        assert response.status_code == 200, response.text
    rejected = _upload_image(admin_client, product["id"], image_type=image_type)
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "image_limit_exceeded"
    assert len(fake_object_storage.put_calls) == limit
