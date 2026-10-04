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

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.base import Base
from app.models import MiniCustomer, MiniSession

NOW = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)


def _migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0005_add_mini_customer_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("mini_auth_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_mini_auth_migration_compiles_offline_with_hashed_session_constraints() -> None:
    migration = _migration()
    assert migration.revision == "0005_add_mini_customer_sessions"
    assert migration.down_revision == "0004_track_promoted_staging_keys"

    output = StringIO()
    context = MigrationContext.configure(
        url="postgresql://",
        opts={"as_sql": True, "output_buffer": output},
    )
    with Operations.context(context):
        migration.upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE mini_customers" in sql
    assert "CREATE TABLE mini_sessions" in sql
    assert "UNIQUE (app_id, openid)" in sql
    assert "UNIQUE (app_id, login_code_hash)" in sql
    assert "UNIQUE (token_hash)" in sql
    assert "REFERENCES mini_customers (id) ON DELETE RESTRICT" in sql
    assert "ck_mini_customers_avatar_metadata_complete" in sql
    assert "session_key" not in sql
    assert "is_admin" not in sql
    assert "REFERENCES users" not in sql

    output.seek(0)
    output.truncate(0)
    with Operations.context(context):
        migration.downgrade()
    assert output.getvalue().index("DROP TABLE mini_sessions") < output.getvalue().index(
        "DROP TABLE mini_customers"
    )


@pytest.fixture
def migrated_connection() -> Iterator[Connection]:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    migration = _migration()
    sentinel = sa.Table(
        "existing_sentinel",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.String(32), nullable=False),
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            sentinel.create(connection)
            connection.execute(sentinel.insert().values(id=7, value="preserved-existing-row"))
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                migration.upgrade()
            assert set(sa.inspect(connection).get_table_names()) == {
                "existing_sentinel",
                "mini_customers",
                "mini_sessions",
            }
            assert connection.scalar(sa.select(sentinel.c.value)) == "preserved-existing-row"
            yield connection
            with Operations.context(context):
                migration.downgrade()
            assert sa.inspect(connection).get_table_names() == ["existing_sentinel"]
            assert connection.execute(sa.select(sentinel)).all() == [(7, "preserved-existing-row")]
    finally:
        engine.dispose()


def test_migrated_schema_matches_the_registered_models(migrated_connection: Connection) -> None:
    inspector = sa.inspect(migrated_connection)
    for model in (MiniCustomer, MiniSession):
        table = model.__table__
        assert Base.metadata.tables[table.name] is table
        actual_columns = inspector.get_columns(table.name)
        assert {
            column["name"]: (
                column["type"].compile(dialect=migrated_connection.dialect),
                column["nullable"],
            )
            for column in actual_columns
        } == {
            column.name: (
                column.type.compile(dialect=migrated_connection.dialect),
                column.nullable,
            )
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


def _seed_customer_and_session(
    connection: Connection,
) -> tuple[sa.Table, sa.Table, int]:
    metadata = sa.MetaData()
    customers = sa.Table("mini_customers", metadata, autoload_with=connection)
    sessions = sa.Table("mini_sessions", metadata, autoload_with=connection)
    customer_id = connection.execute(
        customers.insert().values(app_id="wx-app-one", openid="openid-one")
    ).inserted_primary_key[0]
    assert isinstance(customer_id, int)
    connection.execute(
        sessions.insert().values(
            customer_id=customer_id,
            app_id="wx-app-one",
            token_hash="a" * 64,
            login_code_hash="b" * 64,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    )
    return customers, sessions, customer_id


def test_migration_accepts_customers_and_sessions_with_scoped_identity_and_defaults(
    migrated_connection: Connection,
) -> None:
    customers, sessions, customer_id = _seed_customer_and_session(migrated_connection)
    assert customers.c.nickname.type.length == 64
    customer = migrated_connection.execute(
        sa.select(customers).where(customers.c.id == customer_id)
    ).one()
    assert customer.nickname == "微信用户"
    assert customer.is_active is True
    assert customer.created_at is not None and customer.updated_at is not None
    assert customer.avatar_object_key is None
    assert customer.avatar_sha256 is None
    assert customer.avatar_size_bytes is None
    assert customer.last_login_at is None

    second_customer_id = migrated_connection.execute(
        customers.insert().values(
            app_id="wx-app-two",
            openid="openid-one",
            nickname="👋" * 64,
            avatar_object_key="mini/avatars/complete.png",
            avatar_sha256="c" * 64,
            avatar_size_bytes=1024,
        )
    ).inserted_primary_key[0]
    # Both identity and code reuse are permitted for a different Mini Program AppID.
    migrated_connection.execute(
        sessions.insert().values(
            customer_id=second_customer_id,
            app_id="wx-app-two",
            token_hash="c" * 64,
            login_code_hash="b" * 64,
            created_at=NOW,
            expires_at=NOW + timedelta(hours=1),
        )
    )
    assert migrated_connection.scalar(sa.select(sa.func.count()).select_from(customers)) == 2
    assert migrated_connection.scalar(sa.select(sa.func.count()).select_from(sessions)) == 2


@pytest.mark.parametrize(
    "violation",
    [
        "duplicate_identity",
        "nickname_overlong",
        "duplicate_login_code",
        "duplicate_token",
        "partial_avatar",
        "avatar_size_zero",
        "avatar_hash_short",
        "avatar_key_empty",
        "expiry_at_creation",
        "unknown_customer",
    ],
)
def test_migration_enforces_identity_replay_avatar_and_session_constraints(
    migrated_connection: Connection, violation: str
) -> None:
    customers, sessions, customer_id = _seed_customer_and_session(migrated_connection)
    if violation == "duplicate_identity":
        statement = customers.insert().values(app_id="wx-app-one", openid="openid-one")
    elif violation == "nickname_overlong":
        statement = customers.insert().values(
            app_id="wx-app-one", openid="different-openid", nickname="👋" * 65
        )
    elif violation in {
        "partial_avatar",
        "avatar_size_zero",
        "avatar_hash_short",
        "avatar_key_empty",
    }:
        values: dict[str, object] = {
            "app_id": "wx-app-one",
            "openid": "different-openid",
            "avatar_object_key": "mini/avatars/invalid.png",
        }
        if violation != "partial_avatar":
            values.update(avatar_sha256="c" * 64, avatar_size_bytes=1024)
        if violation == "avatar_size_zero":
            values["avatar_size_bytes"] = 0
        elif violation == "avatar_hash_short":
            values["avatar_sha256"] = "c" * 63
        elif violation == "avatar_key_empty":
            values["avatar_object_key"] = ""
        statement = customers.insert().values(**values)
    else:
        values = {
            "customer_id": customer_id,
            "app_id": "wx-app-one",
            "token_hash": "c" * 64,
            "login_code_hash": "d" * 64,
            "created_at": NOW,
            "expires_at": NOW + timedelta(hours=1),
        }
        if violation == "duplicate_login_code":
            values["login_code_hash"] = "b" * 64
        elif violation == "duplicate_token":
            values["token_hash"] = "a" * 64
        elif violation == "expiry_at_creation":
            values["expires_at"] = NOW
        elif violation == "unknown_customer":
            values["customer_id"] = customer_id + 1000
        statement = sessions.insert().values(**values)

    with pytest.raises(IntegrityError), migrated_connection.begin_nested():
        migrated_connection.execute(statement)

    assert migrated_connection.scalar(sa.select(sa.func.count()).select_from(customers)) == 1
    assert migrated_connection.scalar(sa.select(sa.func.count()).select_from(sessions)) == 1
