from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.base import Base
from app.models import (
    MiniCouponClaim,
    MiniFavorite,
    Product,
    ShopCoupon,
    ShopMedia,
    ShopOrder,
    ShopOrderLine,
    ShopProfile,
)

NOW = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)
SHOP_MODELS = (
    ShopProfile,
    ShopMedia,
    ShopCoupon,
    MiniCouponClaim,
    MiniFavorite,
    ShopOrder,
    ShopOrderLine,
)


def _migration(filename: str = "0006_add_shop_homepage_commerce.py") -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(path.stem, path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _table(connection: Connection, name: str) -> sa.Table:
    return sa.Table(name, sa.MetaData(), autoload_with=connection)


def _seed_existing_catalog(connection: Connection) -> None:
    connection.execute(
        _table(connection, "users")
        .insert()
        .values(id=1, username="shop-admin", password_hash="test-hash", is_admin=True)
    )
    connection.execute(
        _table(connection, "categories").insert().values(id=1, code="coffee", name="咖啡")
    )
    connection.execute(
        _table(connection, "products")
        .insert()
        .values(id=1, product_code="LATTE", name="拿铁", category_id=1, base_price_cents=2000)
    )
    connection.execute(
        _table(connection, "product_skus")
        .insert()
        .values(id=1, product_id=1, sku_code="LATTE-M", name="中杯", price_cents=2000)
    )
    connection.execute(
        _table(connection, "product_images")
        .insert()
        .values(id=1, product_id=1, object_key="catalog/latte.png")
    )
    connection.execute(
        _table(connection, "mini_customers")
        .insert()
        .values(
            [
                {"id": 1, "app_id": "wx-shop", "openid": "customer-one"},
                {"id": 2, "app_id": "wx-shop", "openid": "customer-two"},
            ]
        )
    )


@pytest.fixture
def migrated_connection() -> Iterator[Connection]:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            context = MigrationContext.configure(connection)
            versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
            with Operations.context(context):
                for filename in sorted(versions.glob("000[1-5]_*.py")):
                    _migration(filename.name).upgrade()
            _seed_existing_catalog(connection)
            existing_tables = set(sa.inspect(connection).get_table_names())
            migration = _migration()
            with Operations.context(context):
                migration.upgrade()
            assert set(sa.inspect(connection).get_table_names()) == existing_tables | {
                model.__tablename__ for model in SHOP_MODELS
            }
            assert connection.exec_driver_sql("SELECT search_hit_count FROM products").scalar() == 0
            # Adding the checked column must preserve all pre-existing catalog children.
            assert (
                connection.exec_driver_sql("SELECT sku_code FROM product_skus").scalar()
                == "LATTE-M"
            )
            assert (
                connection.exec_driver_sql("SELECT object_key FROM product_images").scalar()
                == "catalog/latte.png"
            )
            yield connection
            with Operations.context(context):
                migration.downgrade()
            assert set(sa.inspect(connection).get_table_names()) == existing_tables
            assert "search_hit_count" not in {
                column["name"] for column in sa.inspect(connection).get_columns("products")
            }
    finally:
        engine.dispose()


def test_shop_migration_compiles_postgres_offline_without_catalog_rebuild() -> None:
    migration = _migration()
    assert migration.revision == "0006_add_shop_homepage_commerce"
    assert migration.down_revision == "0005_add_mini_customer_sessions"
    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "ALTER TABLE products ADD COLUMN search_hit_count BIGINT DEFAULT 0 NOT NULL" in sql
    assert "CHECK (search_hit_count >= 0)" in sql
    assert "DROP TABLE products" not in sql
    assert "CREATE TABLE products" not in sql
    for model in SHOP_MODELS:
        assert f"CREATE TABLE {model.__tablename__}" in sql
    assert "WHERE kind = 'category' AND is_active" in sql
    assert "WHERE kind = 'announcement' AND is_active" in sql
    assert "REFERENCES products (id) ON DELETE SET NULL" in sql
    assert "REFERENCES users (id) ON DELETE RESTRICT" in sql
    output.seek(0)
    output.truncate(0)
    with Operations.context(context):
        migration.downgrade()
    sql = output.getvalue()
    assert sql.index("DROP TABLE shop_order_lines") < sql.index("DROP TABLE shop_orders")
    assert sql.index("DROP TABLE mini_coupon_claims") < sql.index("DROP TABLE shop_coupons")
    assert "ALTER TABLE products DROP COLUMN search_hit_count" in sql


def test_shop_migration_schema_matches_registered_models(migrated_connection: Connection) -> None:
    inspector = sa.inspect(migrated_connection)
    for model in SHOP_MODELS:
        table = model.__table__
        assert Base.metadata.tables[table.name] is table
        assert {
            column["name"]: (
                column["type"].compile(dialect=migrated_connection.dialect),
                column["nullable"],
            )
            for column in inspector.get_columns(table.name)
        } == {
            column.name: (column.type.compile(dialect=migrated_connection.dialect), column.nullable)
            for column in table.columns
        }
        assert inspector.get_pk_constraint(table.name)["constrained_columns"] == [
            column.name for column in table.primary_key.columns
        ]
        assert {
            constraint["name"]: tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(table.name)
        } == {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }
        assert {
            constraint["name"]: " ".join(constraint["sqltext"].split())
            for constraint in inspector.get_check_constraints(table.name)
        } == {
            constraint.name: " ".join(str(constraint.sqltext).split())
            for constraint in table.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }
        assert {
            index["name"]: (tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table.name)
        } == {
            index.name: (tuple(column.name for column in index.columns), index.unique)
            for index in table.indexes
        }
        assert {
            constraint["name"]: (
                tuple(constraint["constrained_columns"]),
                constraint["referred_table"],
                tuple(constraint["referred_columns"]),
                constraint["options"].get("ondelete"),
            )
            for constraint in inspector.get_foreign_keys(table.name)
        } == {
            constraint.name: (
                tuple(element.parent.name for element in constraint.elements),
                constraint.referred_table.name,
                tuple(element.column.name for element in constraint.elements),
                constraint.ondelete,
            )
            for constraint in table.constraints
            if isinstance(constraint, sa.ForeignKeyConstraint)
        }
    search_count = next(
        c for c in inspector.get_columns("products") if c["name"] == "search_hit_count"
    )
    assert isinstance(search_count["type"], sa.BigInteger)
    assert search_count["nullable"] is False
    assert search_count["default"] == "0"


def _media(**changes: object) -> dict[str, object]:
    return {
        "kind": "carousel",
        "title": "店内新品",
        "media_type": "image",
        "object_key": "shop/carousel.png",
        "mime_type": "image/png",
        "size_bytes": 100,
        "sha256": "a" * 64,
        **changes,
    }


def _coupon(**changes: object) -> dict[str, object]:
    return {
        "title": "满50减5",
        "min_spend_cents": 5000,
        "discount_cents": 500,
        "starts_at": NOW,
        "expires_at": NOW + timedelta(days=1),
        **changes,
    }


def _order(**changes: object) -> dict[str, object]:
    return {
        "external_reference": "receipt-one",
        "customer_id": 1,
        "total_amount_cents": 4000,
        "completed_at": NOW,
        "recorded_by_user_id": 1,
        **changes,
    }


def _line(**changes: object) -> dict[str, object]:
    return {
        "order_id": 1,
        "product_id": 1,
        "product_code": "LATTE",
        "product_name": "拿铁",
        "unit_price_cents": 2000,
        "quantity": 2,
        "line_total_cents": 4000,
        **changes,
    }


@pytest.mark.parametrize(
    "table_name,values",
    [
        ("shop_profiles", {"id": 2}),
        ("shop_profiles", {"id": 1, "name": " "}),
        ("shop_profiles", {"id": 1, "latitude": 90.1}),
        ("shop_profiles", {"id": 1, "longitude": -180.1}),
        ("shop_profiles", {"id": 1, "owner_customer_id": 999}),
        ("shop_media", _media(kind="unknown")),
        ("shop_media", _media(kind="category")),
        ("shop_media", _media(category_id=1)),
        ("shop_media", _media(kind="category", category_id=1, media_type="video")),
        ("shop_media", _media(kind="announcement", media_type="video")),
        ("shop_media", _media(media_type="audio")),
        ("shop_media", _media(size_bytes=0)),
        ("shop_media", _media(sha256="a" * 63)),
        ("shop_media", _media(object_key=" ")),
        ("shop_coupons", _coupon(title="")),
        ("shop_coupons", _coupon(min_spend_cents=-1)),
        ("shop_coupons", _coupon(discount_cents=0)),
        ("shop_coupons", _coupon(discount_cents=5001)),
        ("shop_coupons", _coupon(expires_at=NOW)),
        ("shop_orders", _order(status="pending")),
        ("shop_orders", _order(total_amount_cents=-1)),
        ("shop_orders", _order(status="voided")),
        ("shop_orders", _order(voided_at=NOW)),
        ("shop_orders", _order(status="voided", voided_at=NOW - timedelta(seconds=1))),
        ("shop_orders", _order(customer_id=999)),
        ("shop_orders", _order(recorded_by_user_id=None)),
        ("shop_order_lines", _line(quantity=0, line_total_cents=0)),
        ("shop_order_lines", _line(quantity=100001, line_total_cents=200002000)),
        ("shop_order_lines", _line(unit_price_cents=-1, line_total_cents=-2)),
        ("shop_order_lines", _line(line_total_cents=3999)),
    ],
)
def test_shop_migration_rejects_invalid_rows(
    migrated_connection: Connection, table_name: str, values: dict[str, object]
) -> None:
    if table_name == "shop_order_lines":
        migrated_connection.execute(ShopOrder.__table__.insert().values(id=1, **_order()))
    with pytest.raises(IntegrityError), migrated_connection.begin_nested():
        migrated_connection.execute(
            _table(migrated_connection, table_name).insert().values(**values)
        )


def test_active_media_uniqueness_allows_replacements_and_mixed_carousels(
    migrated_connection: Connection,
) -> None:
    table = ShopMedia.__table__
    for key, kind, category_id, media_type, active in (
        ("active-category", "category", 1, "image", True),
        ("old-category", "category", 1, "image", False),
        ("active-announcement", "announcement", None, "image", True),
        ("old-announcement", "announcement", None, "image", False),
        ("carousel-image", "carousel", None, "image", True),
        ("carousel-video", "carousel", None, "video", True),
    ):
        migrated_connection.execute(
            table.insert().values(
                **_media(
                    object_key=key,
                    kind=kind,
                    category_id=category_id,
                    media_type=media_type,
                    is_active=active,
                )
            )
        )
    for kind, category_id in (("category", 1), ("announcement", None)):
        with pytest.raises(IntegrityError), migrated_connection.begin_nested():
            migrated_connection.execute(
                table.insert().values(
                    **_media(
                        object_key=f"duplicate-{kind}",
                        kind=kind,
                        category_id=category_id,
                    )
                )
            )
    with pytest.raises(IntegrityError), migrated_connection.begin_nested():
        migrated_connection.execute(table.insert().values(**_media(object_key="carousel-image")))
    assert migrated_connection.scalar(sa.select(sa.func.count()).select_from(table)) == 6


def test_coupon_claim_and_favorite_are_unique_and_preserve_claim_history(
    migrated_connection: Connection,
) -> None:
    migrated_connection.execute(ShopCoupon.__table__.insert().values(id=1, **_coupon()))
    migrated_connection.execute(
        MiniCouponClaim.__table__.insert().values(customer_id=1, coupon_id=1)
    )
    migrated_connection.execute(MiniFavorite.__table__.insert().values(customer_id=1, product_id=1))
    for table, values in (
        (MiniCouponClaim.__table__, {"customer_id": 1, "coupon_id": 1}),
        (MiniFavorite.__table__, {"customer_id": 1, "product_id": 1}),
    ):
        with pytest.raises(IntegrityError), migrated_connection.begin_nested():
            migrated_connection.execute(table.insert().values(**values))
    for table in (_table(migrated_connection, "mini_customers"), ShopCoupon.__table__):
        with pytest.raises(IntegrityError), migrated_connection.begin_nested():
            migrated_connection.execute(table.delete().where(table.c.id == 1))
    assert migrated_connection.scalar(sa.select(MiniCouponClaim.claimed_at)) is not None


def test_owner_and_favorites_follow_customer_deletion(migrated_connection: Connection) -> None:
    migrated_connection.execute(ShopProfile.__table__.insert().values(id=1, owner_customer_id=2))
    migrated_connection.execute(MiniFavorite.__table__.insert().values(customer_id=2, product_id=1))
    customers = _table(migrated_connection, "mini_customers")
    migrated_connection.execute(customers.delete().where(customers.c.id == 2))
    profile = migrated_connection.execute(sa.select(ShopProfile.__table__)).one()
    assert profile.name == "港湾集市"
    assert profile.phone == profile.address == ""
    assert profile.owner_customer_id is None
    assert migrated_connection.scalar(sa.select(sa.func.count()).select_from(MiniFavorite)) == 0


def test_historical_order_snapshots_survive_product_deletion_and_anonymous_orders(
    migrated_connection: Connection,
) -> None:
    with Session(migrated_connection) as session:
        order = ShopOrder(**_order())
        order.items.append(ShopOrderLine(**{k: v for k, v in _line().items() if k != "order_id"}))
        session.add(order)
        session.flush()
        assert order.items[0].order_id == order.id
        assert order.status == "completed"
        anonymous = ShopOrder(**_order(external_reference="receipt-anonymous", customer_id=None))
        session.add(anonymous)
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(ShopOrder(**_order()))
            session.flush()
        session.add(MiniFavorite(customer_id=1, product_id=1))
        session.flush()
        product = session.get(Product, 1)
        assert product is not None and product.search_hit_count == 0
        session.delete(product)
        session.flush()
        session.expire_all()
        line = session.get(ShopOrderLine, order.items[0].id)
        assert line is not None
        assert line.product_id is None
        assert (line.product_code, line.product_name, line.line_total_cents) == (
            "LATTE",
            "拿铁",
            4000,
        )
        assert session.scalar(sa.select(sa.func.count()).select_from(MiniFavorite)) == 0
        for table_name in ("users", "mini_customers"):
            table = _table(migrated_connection, table_name)
            with pytest.raises(IntegrityError), session.begin_nested():
                session.execute(table.delete().where(table.c.id == 1))
        session.delete(order)
        session.flush()
        assert session.scalar(sa.select(sa.func.count()).select_from(ShopOrderLine)) == 0


def test_search_hit_counter_rejects_negative_and_supports_bigint(
    migrated_connection: Connection,
) -> None:
    migrated_connection.execute(
        sa.update(Product).where(Product.id == 1).values(search_hit_count=2**32)
    )
    assert migrated_connection.scalar(sa.select(Product.search_hit_count)) == 2**32
    with pytest.raises(IntegrityError), migrated_connection.begin_nested():
        migrated_connection.execute(sa.update(Product).values(search_hit_count=-1))
