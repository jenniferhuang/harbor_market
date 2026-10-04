"""Add shop homepage content, customer interactions, and historical purchases.

Revision ID: 0006_add_shop_homepage_commerce
Revises: 0005_add_mini_customer_sessions
Create Date: 2026-10-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_add_shop_homepage_commerce"
down_revision: str | None = "0005_add_mini_customer_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def upgrade() -> None:
    # SQLite supports adding a checked column directly. Avoid rebuilding products,
    # because dropping the original table could cascade-delete its existing children.
    if op.get_context().dialect.name == "sqlite":
        op.add_column(
            "products",
            sa.Column(
                "search_hit_count",
                sa.BigInteger(),
                sa.CheckConstraint(
                    "search_hit_count >= 0",
                    name=op.f("ck_products_search_hit_count_nonnegative"),
                ),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )
    else:
        op.add_column(
            "products",
            sa.Column(
                "search_hit_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False
            ),
        )
        op.create_check_constraint(
            op.f("ck_products_search_hit_count_nonnegative"), "products", "search_hit_count >= 0"
        )

    op.create_table(
        "shop_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), server_default=sa.text("'港湾集市'"), nullable=False),
        sa.Column("phone", sa.String(40), server_default=sa.text("''"), nullable=False),
        sa.Column("address", sa.String(500), server_default=sa.text("''"), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("owner_customer_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("id = 1", name=op.f("ck_shop_profiles_singleton")),
        sa.CheckConstraint(
            "length(trim(name)) BETWEEN 1 AND 160", name=op.f("ck_shop_profiles_name_length")
        ),
        sa.CheckConstraint("length(phone) <= 40", name=op.f("ck_shop_profiles_phone_length")),
        sa.CheckConstraint("length(address) <= 500", name=op.f("ck_shop_profiles_address_length")),
        sa.CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name=op.f("ck_shop_profiles_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name=op.f("ck_shop_profiles_longitude_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_customer_id"],
            ["mini_customers.id"],
            name=op.f("fk_shop_profiles_owner_customer_id_mini_customers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_profiles")),
    )
    op.create_table(
        "shop_media",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(160), server_default=sa.text("''"), nullable=False),
        sa.Column("media_type", sa.String(20), nullable=False),
        sa.Column("object_key", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "kind IN ('carousel', 'category', 'announcement')",
            name=op.f("ck_shop_media_kind_allowed"),
        ),
        sa.CheckConstraint(
            "(kind = 'category' AND category_id IS NOT NULL) "
            "OR (kind IN ('carousel', 'announcement') AND category_id IS NULL)",
            name=op.f("ck_shop_media_category_scope"),
        ),
        sa.CheckConstraint("length(title) <= 160", name=op.f("ck_shop_media_title_length")),
        sa.CheckConstraint(
            "media_type IN ('image', 'video')", name=op.f("ck_shop_media_media_type_allowed")
        ),
        sa.CheckConstraint(
            "kind = 'carousel' OR media_type = 'image'",
            name=op.f("ck_shop_media_noncarousel_image_only"),
        ),
        sa.CheckConstraint(
            "length(trim(object_key)) BETWEEN 1 AND 512",
            name=op.f("ck_shop_media_object_key_length"),
        ),
        sa.CheckConstraint(
            "length(trim(mime_type)) BETWEEN 1 AND 100", name=op.f("ck_shop_media_mime_type_length")
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_shop_media_size_positive")),
        sa.CheckConstraint("length(sha256) = 64", name=op.f("ck_shop_media_sha256_length")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_shop_media_category_id_categories"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_media")),
        sa.UniqueConstraint("object_key", name=op.f("uq_shop_media_object_key")),
    )
    op.create_index(
        "ix_shop_media_kind_active_sort", "shop_media", ["kind", "is_active", "sort_order"]
    )
    op.create_index(
        "uq_shop_media_one_active_category",
        "shop_media",
        ["category_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'category' AND is_active"),
        sqlite_where=sa.text("kind = 'category' AND is_active = 1"),
    )
    op.create_index(
        "uq_shop_media_one_active_announcement",
        "shop_media",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'announcement' AND is_active"),
        sqlite_where=sa.text("kind = 'announcement' AND is_active = 1"),
    )
    op.create_table(
        "shop_coupons",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("min_spend_cents", sa.Integer(), nullable=False),
        sa.Column("discount_cents", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "length(trim(title)) BETWEEN 1 AND 160", name=op.f("ck_shop_coupons_title_length")
        ),
        sa.CheckConstraint(
            "min_spend_cents >= 0", name=op.f("ck_shop_coupons_min_spend_nonnegative")
        ),
        sa.CheckConstraint("discount_cents > 0", name=op.f("ck_shop_coupons_discount_positive")),
        sa.CheckConstraint(
            "discount_cents <= min_spend_cents",
            name=op.f("ck_shop_coupons_discount_not_above_minimum"),
        ),
        sa.CheckConstraint(
            "expires_at > starts_at", name=op.f("ck_shop_coupons_expiry_after_start")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_coupons")),
    )
    op.create_index(
        "ix_shop_coupons_active_window", "shop_coupons", ["is_active", "starts_at", "expires_at"]
    )
    op.create_table(
        "mini_coupon_claims",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("coupon_id", sa.Integer(), nullable=False),
        sa.Column(
            "claimed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["mini_customers.id"],
            name=op.f("fk_mini_coupon_claims_customer_id_mini_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["coupon_id"],
            ["shop_coupons.id"],
            name=op.f("fk_mini_coupon_claims_coupon_id_shop_coupons"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mini_coupon_claims")),
        sa.UniqueConstraint(
            "customer_id", "coupon_id", name="uq_mini_coupon_claims_customer_coupon"
        ),
    )
    op.create_index("ix_mini_coupon_claims_coupon", "mini_coupon_claims", ["coupon_id"])
    op.create_table(
        "mini_favorites",
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["mini_customers.id"],
            name=op.f("fk_mini_favorites_customer_id_mini_customers"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_mini_favorites_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("customer_id", "product_id", name=op.f("pk_mini_favorites")),
    )
    op.create_index("ix_mini_favorites_product", "mini_favorites", ["product_id"])
    op.create_table(
        "shop_orders",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("external_reference", sa.String(100), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), server_default=sa.text("'completed'"), nullable=False),
        sa.Column("total_amount_cents", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("recorded_by_user_id", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(trim(external_reference)) BETWEEN 1 AND 100",
            name=op.f("ck_shop_orders_external_reference_length"),
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'voided')", name=op.f("ck_shop_orders_status_allowed")
        ),
        sa.CheckConstraint(
            "total_amount_cents >= 0", name=op.f("ck_shop_orders_total_nonnegative")
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND voided_at IS NULL) "
            "OR (status = 'voided' AND voided_at IS NOT NULL AND voided_at >= completed_at)",
            name=op.f("ck_shop_orders_void_state"),
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["mini_customers.id"],
            name=op.f("fk_shop_orders_customer_id_mini_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by_user_id"],
            ["users.id"],
            name=op.f("fk_shop_orders_recorded_by_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_orders")),
        sa.UniqueConstraint("external_reference", name=op.f("uq_shop_orders_external_reference")),
    )
    op.create_index(
        "ix_shop_orders_customer_status_completed",
        "shop_orders",
        ["customer_id", "status", "completed_at"],
    )
    op.create_index("ix_shop_orders_status_completed", "shop_orders", ["status", "completed_at"])
    op.create_table(
        "shop_order_lines",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=True),
        sa.Column("product_code", sa.String(64), nullable=False),
        sa.Column("product_name", sa.String(160), nullable=False),
        sa.Column("unit_price_cents", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("line_total_cents", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(trim(product_code)) BETWEEN 1 AND 64",
            name=op.f("ck_shop_order_lines_product_code_length"),
        ),
        sa.CheckConstraint(
            "length(trim(product_name)) BETWEEN 1 AND 160",
            name=op.f("ck_shop_order_lines_product_name_length"),
        ),
        sa.CheckConstraint(
            "unit_price_cents >= 0", name=op.f("ck_shop_order_lines_unit_price_nonnegative")
        ),
        sa.CheckConstraint(
            "quantity BETWEEN 1 AND 100000", name=op.f("ck_shop_order_lines_quantity_range")
        ),
        sa.CheckConstraint(
            "line_total_cents >= 0", name=op.f("ck_shop_order_lines_line_total_nonnegative")
        ),
        sa.CheckConstraint(
            "line_total_cents = unit_price_cents * quantity",
            name=op.f("ck_shop_order_lines_line_total_matches_price"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["shop_orders.id"],
            name=op.f("fk_shop_order_lines_order_id_shop_orders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_shop_order_lines_product_id_products"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_order_lines")),
    )
    op.create_index("ix_shop_order_lines_order", "shop_order_lines", ["order_id"])
    op.create_index("ix_shop_order_lines_product", "shop_order_lines", ["product_id"])


def downgrade() -> None:
    op.drop_table("shop_order_lines")
    op.drop_table("shop_orders")
    op.drop_table("mini_favorites")
    op.drop_table("mini_coupon_claims")
    op.drop_table("shop_coupons")
    op.drop_table("shop_media")
    op.drop_table("shop_profiles")
    if op.get_context().dialect.name == "sqlite":
        # DROP COLUMN also removes the inline column constraint on supported SQLite.
        op.drop_column("products", "search_hit_count")
    else:
        op.drop_constraint(
            op.f("ck_products_search_hit_count_nonnegative"), "products", type_="check"
        )
        op.drop_column("products", "search_hit_count")
