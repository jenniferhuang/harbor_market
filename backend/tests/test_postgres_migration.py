from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.postgres


def test_initial_migration_and_case_insensitive_uniqueness() -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    database_name = make_url(database_url).database or ""
    if "test" not in database_name.casefold():
        pytest.skip("TEST_DATABASE_URL database name must contain 'test'")

    backend_dir = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "DATABASE_URL": database_url}
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
        "created_at",
        "updated_at",
        "last_login_at",
    }

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
