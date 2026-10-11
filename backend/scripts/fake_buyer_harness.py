"""Run the isolated, local-only buyer UI fixture server.

From the repository root:
    backend/.venv/bin/python backend/scripts/fake_buyer_harness.py

The bind address, port, SQLite path, and fake identity are fixed. Settings come
only from the values below: no environment, .env, or secret-file configuration
is used. Run this script in its own process; never import it into app runtime.
Favorites, coupon claims, and sessions persist in the isolated SQLite database.
Images live in memory and are regenerated when the harness starts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
DATA_ROOT = REPOSITORY / ".data"
DATA_DIRECTORY = DATA_ROOT / "fake-buyer-ui"
DATABASE = DATA_DIRECTORY / "ui-harness.sqlite"
SEED_SUMMARY = DATA_DIRECTORY / "ui-seed-summary.json"
HOST = "127.0.0.1"
PORT = 18080
TEST_APP_ID = "wx1234567890abcdef"
TEST_OPENID = "isolated-ui-e2e-customer"
TEST_ADMIN_PASSWORD = "isolated-e2e-admin-password"
TEST_SIGNING_SECRET = "isolated-e2e-test-signing-secret-at-least-32-characters"

# The script can be run from any working directory without loading its .env.
sys.path.insert(0, str(BACKEND))

# Imports follow the path bootstrap; app.main is intentionally imported later.
# ruff: noqa: E402
from fastapi import FastAPI
from PIL import Image, ImageDraw
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import config
from app.core.config import Settings
from app.core.security import PasswordManager
from app.db.base import Base
from app.models import (
    Category,
    MiniCustomer,
    Product,
    ProductImage,
    ProductSku,
    ShopCoupon,
    ShopProfile,
    User,
)
from app.services.object_storage import (
    ObjectStat,
    ObjectStorageNotFoundError,
    ObjectStorageSizeError,
)
from app.wechat.auth import WechatIdentity


class HarnessSettings(Settings):
    """Disable every configuration source except explicit constructor values."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


class SingleTestCustomerProvider:
    """Every fresh wx.login code resolves to the same isolated buyer."""

    def exchange_code(self, _code: str) -> WechatIdentity:
        return WechatIdentity(app_id=TEST_APP_ID, openid=TEST_OPENID)


class MemoryObjectStorage:
    """Local image storage; no MinIO, filesystem, or external credentials."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.stats: dict[str, ObjectStat] = {}

    def ensure_bucket(self) -> None:
        return None

    def put(
        self,
        object_name: str,
        data: BinaryIO | bytes,
        length: int | None = None,
        *,
        content_type: str = "application/octet-stream",
        metadata: Mapping[str, str] | None = None,
    ) -> ObjectStat:
        payload = data if isinstance(data, bytes) else data.read()
        if length is not None and length != len(payload):
            raise ValueError("length does not match the byte payload")
        result = ObjectStat(
            object_name=object_name,
            size=len(payload),
            content_type=content_type,
            etag=hashlib.sha256(payload).hexdigest(),
            last_modified=datetime.now(UTC),
            metadata=dict(metadata or {}),
        )
        self.objects[object_name] = payload
        self.stats[object_name] = result
        return result

    def get(self, object_name: str, *, max_bytes: int | None = None) -> bytes:
        self.stat(object_name)
        payload = self.objects[object_name]
        if max_bytes is not None and len(payload) > max_bytes:
            raise ObjectStorageSizeError("image exceeds the bounded read limit")
        return payload

    def copy(
        self,
        source_name: str,
        destination_name: str,
        *,
        source_etag: str | None = None,
    ) -> ObjectStat:
        source = self.stat(source_name)
        if source_etag is not None and source.etag != source_etag:
            raise RuntimeError("image etag precondition failed")
        return self.put(
            destination_name,
            self.get(source_name),
            content_type=source.content_type or "application/octet-stream",
            metadata=source.metadata,
        )

    def stat(self, object_name: str) -> ObjectStat:
        if object_name not in self.stats:
            raise ObjectStorageNotFoundError(object_name)
        return self.stats[object_name]

    def delete(self, object_name: str) -> None:
        self.stat(object_name)
        del self.objects[object_name]
        del self.stats[object_name]

    def object_exists(self, object_name: str) -> bool:
        return object_name in self.objects


def _check_local_paths() -> None:
    """Refuse symlinks before creating the fixed isolated data directory."""
    paths = (
        DATA_ROOT,
        DATA_DIRECTORY,
        DATABASE,
        SEED_SUMMARY,
        Path(f"{DATABASE}-journal"),
        Path(f"{DATABASE}-wal"),
        Path(f"{DATABASE}-shm"),
    )
    for path in paths:
        if path.is_symlink():
            raise RuntimeError(f"Isolated harness path must not be a symlink: {path}")
        if not path.resolve().is_relative_to(DATA_ROOT):
            raise RuntimeError("Isolated harness path must remain under repository .data")
    for directory in (DATA_ROOT, DATA_DIRECTORY):
        if directory.exists() and not directory.is_dir():
            raise RuntimeError(f"Isolated harness directory is not a directory: {directory}")
    for path in paths[2:]:
        if path.exists() and not path.is_file():
            raise RuntimeError(f"Isolated harness file is not a regular file: {path}")


def _seed_fixtures(
    session: Session, settings: Settings, storage: MemoryObjectStorage
) -> dict[str, object]:
    customer = session.scalar(
        select(MiniCustomer).where(
            MiniCustomer.app_id == TEST_APP_ID,
            MiniCustomer.openid == TEST_OPENID,
        )
    )
    if customer is None:
        customer = MiniCustomer(
            app_id=TEST_APP_ID, openid=TEST_OPENID, nickname="E2E测试用户"
        )
        session.add(customer)

    if session.scalar(select(User).where(User.username == "e2e-admin")) is None:
        session.add(
            User(
                username="e2e-admin",
                password_hash=PasswordManager(settings).hash(TEST_ADMIN_PASSWORD),
                is_admin=True,
            )
        )

    category = session.scalar(select(Category).where(Category.code == "E2E"))
    if category is None:
        category = Category(code="E2E", name="隔离测试饮品")
        session.add(category)
    category.is_active = True
    session.flush()

    specifications = [
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
            ],
        },
    ]
    products = (
        ("E2E-COFFEE", "测试咖啡", 400, True, specifications, "#b77744"),
        ("E2E-TEA", "测试原味茶", 250, True, [], "#388a72"),
        ("E2E-SOLDOUT", "测试售罄饮品", 350, False, [], "#888888"),
    )
    for code, name, price, in_stock, specs, color in products:
        product = session.scalar(select(Product).where(Product.product_code == code))
        if product is None:
            product = Product(product_code=code, name=name, category=category)
            session.add(product)
        product.name = name
        product.category = category
        product.status = "published"
        product.base_price_cents = price
        product.market_price_cents = None
        product.stock_status = "in_stock" if in_stock else "out_of_stock"
        product.featured = in_stock
        product.inventory_count = 99 if in_stock else 0
        product.specifications = specs
        product.description = "仅用于隔离环境端到端测试的商品。"
        session.flush()

        variants = (
            ("SMALL", "小杯", 400, {"cup": "small"}, True),
            ("LARGE", "大杯", 550, {"cup": "large"}, False),
        ) if specs else (("DEFAULT", "默认规格", price, {}, True),)
        for suffix, sku_name, sku_price, attributes, is_default in variants:
            sku_code = f"{code}-{suffix}"
            sku = session.scalar(select(ProductSku).where(ProductSku.sku_code == sku_code))
            if sku is None:
                sku = ProductSku(product=product, sku_code=sku_code, name=sku_name)
                session.add(sku)
            sku.name = sku_name
            sku.price_cents = sku_price
            sku.market_price_cents = None
            sku.stock_quantity = 99 if in_stock else 0
            sku.attributes = attributes
            sku.is_default = is_default
            sku.is_active = True

        image = Image.new("RGB", (320, 320), color)
        ImageDraw.Draw(image).text((24, 145), code, fill="white")
        content = BytesIO()
        image.save(content, format="PNG")
        payload = content.getvalue()
        object_key = f"products/isolated-e2e/{code}/cover.png"
        storage.put(object_key, payload, content_type="image/png")
        if session.scalar(select(ProductImage).where(ProductImage.object_key == object_key)) is None:
            session.add(
                ProductImage(
                    product=product,
                    object_key=object_key,
                    image_type="cover",
                    alt_text="E2E测试图片",
                    mime_type="image/png",
                    size_bytes=len(payload),
                    width=320,
                    height=320,
                )
            )

    now = datetime.now(UTC)
    coupon = session.scalar(select(ShopCoupon).where(ShopCoupon.title == "测试满10元减2元"))
    if coupon is None:
        coupon = ShopCoupon(
            title="测试满10元减2元",
            starts_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=7),
        )
        session.add(coupon)
    else:
        # SQLite returns naive timestamps; the application stores them as UTC.
        starts_at = coupon.starts_at.replace(tzinfo=UTC)
        expires_at = coupon.expires_at.replace(tzinfo=UTC)
        if expires_at <= now or starts_at > now:
            coupon.starts_at = now - timedelta(days=1)
            coupon.expires_at = now + timedelta(days=7)
    coupon.min_spend_cents = 1000
    coupon.discount_cents = 200
    coupon.is_active = True
    profile = session.get(ShopProfile, 1)
    if profile is None:
        session.add(ShopProfile(id=1, name="隔离测试店铺"))
    else:
        profile.name = "隔离测试店铺"
    session.commit()
    return {
        "base_url": f"http://{HOST}:{PORT}",
        "database_path": str(DATABASE),
        "database_kind": "isolated_sqlite",
        "customer_id": customer.id,
        "customer_nickname": customer.nickname,
        "product_codes": [row[0] for row in products],
        "coupon_id": coupon.id,
        "coupon_min_spend_cents": 1000,
        "coupon_discount_cents": 200,
        "wechat_provider": "injected_single_test_customer",
        "payment_mode": "disabled",
        "sample_cart": [
            {
                "product_code": "E2E-COFFEE",
                "sku_code": "E2E-COFFEE-LARGE",
                "selections": {"cup": ["large"], "addons": ["pearls", "milk"]},
                "quantity": 2,
            },
            {
                "product_code": "E2E-TEA",
                "sku_code": "E2E-TEA-DEFAULT",
                "selections": {},
                "quantity": 1,
            },
        ],
        "sample_quote_cents": {"subtotal": 1650, "discount": 200, "total": 1450},
    }


def build_app() -> tuple[FastAPI, dict[str, object]]:
    """Create only the fixed local fixture; external overrides are unsupported."""
    if "app.main" in sys.modules:
        raise RuntimeError("Run the fake buyer harness in a fresh, separate Python process")
    _check_local_paths()
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    _check_local_paths()
    settings = HarnessSettings(
        _env_file=None,
        _secrets_dir=None,
        app_name="Harbor Market Isolated Buyer UI Harness",
        environment="test",
        database_url=f"sqlite+pysqlite:///{DATABASE}",
        auth_secret_key=TEST_SIGNING_SECRET,
        auth_cookie_secure=False,
        allowed_hosts=HOST,
        cors_allowed_origins=f"http://{HOST}:{PORT}",
        wechat_miniprogram_auth_mode="disabled",
        wechat_miniprogram_app_id=TEST_APP_ID,
        storage_backend="disabled",
        payment_mode="disabled",
        payment_mock_controls_enabled=False,
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
        mini_login_rate_limit=100,
        login_failure_rate_limit=100,
    )

    # app.main creates a default app during import. Inject this validated,
    # init-only configuration first so that default cannot read production .env.
    def harness_settings() -> Settings:
        return settings

    config.get_settings = harness_settings
    from app.main import app as default_app
    from app.main import create_app

    engine = default_app.state.engine
    Base.metadata.create_all(engine)
    storage = MemoryObjectStorage()
    application = create_app(
        settings,
        engine=engine,
        object_storage=storage,
        wechat_auth_provider=SingleTestCustomerProvider(),
    )
    with application.state.session_factory() as session:
        summary = _seed_fixtures(session, settings, storage)
    return application, summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # No database, host, port, or environment override flags.
    application, summary = build_app()
    _check_local_paths()
    SEED_SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)

    import uvicorn

    uvicorn.run(application, host=HOST, port=PORT, access_log=True)


if __name__ == "__main__":
    main()
