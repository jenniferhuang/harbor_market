from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.postgres


def _postgres_test_context() -> tuple[str, Path, dict[str, str]]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    database_name = make_url(database_url).database or ""
    if "test" not in database_name.casefold():
        pytest.skip("TEST_DATABASE_URL database name must contain 'test'")

    backend_dir = Path(__file__).resolve().parents[1]
    # Keep subprocess tracebacks from rendering unrelated host credentials when
    # Alembic fails. The migration only needs its venv PATH, locale, and DB URL.
    environment = {
        "DATABASE_URL": database_url,
        "PATH": f"{backend_dir / '.venv' / 'bin'}:/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    return database_url, backend_dir, environment


def test_initial_migration_and_case_insensitive_uniqueness() -> None:
    database_url, backend_dir, environment = _postgres_test_context()
    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        check=True,
    )

    engine = sa.create_engine(database_url)
    inspector = sa.inspect(engine)
    assert {column["name"] for column in inspector.get_columns("users")} >= {
        "id",
        "username",
        "password_hash",
        "is_active",
        "is_admin",
        "created_at",
        "updated_at",
        "last_login_at",
    }
    assert {
        "users",
        "categories",
        "products",
        "product_skus",
        "product_images",
        "import_jobs",
        "object_cleanup_jobs",
    } <= set(inspector.get_table_names())
    assert {column["name"] for column in inspector.get_columns("categories")} >= {
        "code",
        "name",
        "description",
        "parent_id",
        "sort_order",
        "is_active",
    }
    assert {column["name"] for column in inspector.get_columns("products")} >= {
        "product_code",
        "category_id",
        "status",
        "base_price_cents",
        "market_price_cents",
        "tags",
        "selling_points",
        "specifications",
    }
    assert {column["name"] for column in inspector.get_columns("product_images")} >= {
        "product_id",
        "object_key",
        "image_type",
        "mime_type",
        "size_bytes",
        "width",
        "height",
    }
    assert {column["name"] for column in inspector.get_columns("object_cleanup_jobs")} >= {
        "created_by",
        "object_key",
        "reason",
        "status",
        "attempts",
        "last_error",
        "not_before",
        "completed_at",
    }
    assert {column["name"] for column in inspector.get_columns("import_jobs")} >= {
        "created_by",
        "original_filename",
        "workbook_sha256",
        "idempotency_key",
        "status",
        "summary",
        "errors",
    }
    assert {
        "ix_import_jobs_workbook_sha256",
        "uq_import_jobs_creator_idempotency_key",
    } <= {index["name"] for index in inspector.get_indexes("import_jobs")}
    assert {
        "ix_object_cleanup_jobs_creator_created",
        "ix_object_cleanup_jobs_status_created",
    } <= {index["name"] for index in inspector.get_indexes("object_cleanup_jobs")}

    users = sa.table(
        "users",
        sa.column("username"),
        sa.column("password_hash"),
    )
    try:
        with engine.begin() as connection:
            connection.execute(users.delete())
            connection.execute(
                users.insert().values(username="CaseUser", password_hash="$argon2id$test")
            )
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                users.insert().values(username="caseuser", password_hash="$argon2id$test")
            )
    finally:
        with engine.begin() as connection:
            connection.execute(users.delete())
        engine.dispose()


def test_catalog_migration_downgrades_to_0001_and_upgrades_again() -> None:
    database_url, backend_dir, environment = _postgres_test_context()
    subprocess.run(
        ["alembic", "downgrade", "0001_create_users"],
        cwd=backend_dir,
        env=environment,
        check=True,
    )

    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        assert set(inspector.get_table_names()) == {"alembic_version", "users"}
        assert "is_admin" not in {column["name"] for column in inspector.get_columns("users")}
    finally:
        engine.dispose()

    subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        check=True,
    )
    engine = sa.create_engine(database_url)
    try:
        inspector = sa.inspect(engine)
        assert {
            "users",
            "categories",
            "products",
            "product_skus",
            "product_images",
            "import_jobs",
            "object_cleanup_jobs",
        } <= set(inspector.get_table_names())
        assert "is_admin" in {column["name"] for column in inspector.get_columns("users")}
    finally:
        engine.dispose()
