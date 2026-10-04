"""Add isolated Mini Program customer identities and opaque sessions.

Revision ID: 0005_add_mini_customer_sessions
Revises: 0004_track_promoted_staging_keys
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_add_mini_customer_sessions"
down_revision: str | None = "0004_track_promoted_staging_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mini_customers",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("app_id", sa.String(64), nullable=False),
        sa.Column("openid", sa.String(128), nullable=False),
        sa.Column("nickname", sa.String(64), server_default=sa.text("'微信用户'"), nullable=False),
        sa.Column("avatar_object_key", sa.String(512), nullable=True),
        sa.Column("avatar_sha256", sa.String(64), nullable=True),
        sa.Column("avatar_size_bytes", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(app_id) BETWEEN 1 AND 64", name=op.f("ck_mini_customers_app_id_length")
        ),
        sa.CheckConstraint(
            "length(openid) BETWEEN 1 AND 128", name=op.f("ck_mini_customers_openid_length")
        ),
        sa.CheckConstraint(
            "length(trim(nickname)) BETWEEN 1 AND 64",
            name=op.f("ck_mini_customers_nickname_length"),
        ),
        sa.CheckConstraint(
            "(avatar_object_key IS NULL AND avatar_sha256 IS NULL AND avatar_size_bytes IS NULL) "
            "OR (avatar_object_key IS NOT NULL AND avatar_sha256 IS NOT NULL "
            "AND avatar_size_bytes IS NOT NULL AND length(avatar_object_key) BETWEEN 1 AND 512 "
            "AND length(avatar_sha256) = 64 AND avatar_size_bytes > 0)",
            name=op.f("ck_mini_customers_avatar_metadata_complete"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mini_customers")),
        sa.UniqueConstraint("app_id", "openid", name="uq_mini_customers_app_openid"),
    )
    op.create_table(
        "mini_sessions",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("app_id", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("login_code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(app_id) BETWEEN 1 AND 64", name=op.f("ck_mini_sessions_app_id_length")
        ),
        sa.CheckConstraint(
            "length(token_hash) = 64", name=op.f("ck_mini_sessions_token_hash_length")
        ),
        sa.CheckConstraint(
            "length(login_code_hash) = 64", name=op.f("ck_mini_sessions_login_code_hash_length")
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_mini_sessions_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["mini_customers.id"],
            name=op.f("fk_mini_sessions_customer_id_mini_customers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mini_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_mini_sessions_token_hash")),
        sa.UniqueConstraint("app_id", "login_code_hash", name="uq_mini_sessions_app_login_code"),
    )
    op.create_index(
        "ix_mini_sessions_customer_created", "mini_sessions", ["customer_id", "created_at"]
    )
    op.create_index("ix_mini_sessions_expires_at", "mini_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_mini_sessions_expires_at", table_name="mini_sessions")
    op.drop_index("ix_mini_sessions_customer_created", table_name="mini_sessions")
    op.drop_table("mini_sessions")
    op.drop_table("mini_customers")
