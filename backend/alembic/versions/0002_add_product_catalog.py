"""Add product catalog and import job tables.

Revision ID: 0002_add_product_catalog
Revises: 0001_create_users
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_add_product_catalog"
down_revision: str | None = "0001_create_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(code)) BETWEEN 1 AND 50",
            name=op.f("ck_categories_code_length"),
        ),
        sa.CheckConstraint(
            "length(trim(name)) BETWEEN 1 AND 100",
            name=op.f("ck_categories_name_length"),
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name=op.f("ck_categories_parent_not_self"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["categories.id"],
            name=op.f("fk_categories_parent_id_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_categories")),
        sa.UniqueConstraint("code", name=op.f("uq_categories_code")),
    )
    op.create_index(
        "ix_categories_parent_active_sort",
        "categories",
        ["parent_id", "is_active", "sort_order"],
        unique=False,
    )

    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("product_code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("subtitle", sa.String(length=240), nullable=True),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("base_price_cents", sa.Integer(), nullable=False),
        sa.Column("market_price_cents", sa.Integer(), nullable=True),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'CNY'"),
            nullable=False,
        ),
        sa.Column(
            "unit",
            sa.String(length=20),
            server_default=sa.text("'件'"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "featured",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "stock_status",
            sa.String(length=20),
            server_default=sa.text("'in_stock'"),
            nullable=False,
        ),
        sa.Column("inventory_count", sa.Integer(), nullable=True),
        sa.Column("tags", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "selling_points",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column(
            "specifications",
            sa.JSON(),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        sa.Column("ingredients", sa.Text(), nullable=True),
        sa.Column("allergen_info", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(product_code)) BETWEEN 1 AND 64",
            name=op.f("ck_products_product_code_length"),
        ),
        sa.CheckConstraint(
            "length(trim(name)) BETWEEN 1 AND 160",
            name=op.f("ck_products_name_length"),
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name=op.f("ck_products_status_allowed"),
        ),
        sa.CheckConstraint(
            "base_price_cents >= 0",
            name=op.f("ck_products_base_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "market_price_cents IS NULL OR market_price_cents >= base_price_cents",
            name=op.f("ck_products_market_price_not_below_base"),
        ),
        sa.CheckConstraint(
            "length(trim(unit)) BETWEEN 1 AND 20",
            name=op.f("ck_products_unit_length"),
        ),
        sa.CheckConstraint(
            "stock_status IN ('in_stock', 'out_of_stock', 'preorder')",
            name=op.f("ck_products_stock_status_allowed"),
        ),
        sa.CheckConstraint(
            "inventory_count IS NULL OR inventory_count >= 0",
            name=op.f("ck_products_inventory_count_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["categories.id"],
            name=op.f("fk_products_category_id_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("product_code", name=op.f("uq_products_product_code")),
    )
    op.create_index(
        "ix_products_category_status_sort",
        "products",
        ["category_id", "status", "sort_order"],
        unique=False,
    )
    op.create_index(
        "ix_products_status_featured_sort",
        "products",
        ["status", "featured", "sort_order"],
        unique=False,
    )

    op.create_table(
        "product_skus",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("sku_code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("market_price_cents", sa.Integer(), nullable=True),
        sa.Column(
            "stock_quantity",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("attributes", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(sku_code)) BETWEEN 1 AND 80",
            name=op.f("ck_product_skus_sku_code_length"),
        ),
        sa.CheckConstraint(
            "length(trim(name)) BETWEEN 1 AND 160",
            name=op.f("ck_product_skus_name_length"),
        ),
        sa.CheckConstraint(
            "price_cents >= 0",
            name=op.f("ck_product_skus_price_nonnegative"),
        ),
        sa.CheckConstraint(
            "market_price_cents IS NULL OR market_price_cents >= price_cents",
            name=op.f("ck_product_skus_market_price_not_below_price"),
        ),
        sa.CheckConstraint(
            "stock_quantity >= 0",
            name=op.f("ck_product_skus_stock_quantity_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_skus_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_skus")),
        sa.UniqueConstraint("sku_code", name=op.f("uq_product_skus_sku_code")),
    )
    op.create_index(
        "ix_product_skus_product_active_sort",
        "product_skus",
        ["product_id", "is_active", "sort_order"],
        unique=False,
    )
    op.create_index(
        "uq_product_skus_one_active_default",
        "product_skus",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("is_default AND is_active"),
        sqlite_where=sa.text("is_default = 1 AND is_active = 1"),
    )

    op.create_table(
        "product_images",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column(
            "image_type",
            sa.String(length=20),
            server_default=sa.text("'gallery'"),
            nullable=False,
        ),
        sa.Column("alt_text", sa.String(length=200), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(trim(object_key)) BETWEEN 1 AND 512",
            name=op.f("ck_product_images_object_key_length"),
        ),
        sa.CheckConstraint(
            "image_type IN ('cover', 'gallery', 'detail')",
            name=op.f("ck_product_images_image_type_allowed"),
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0",
            name=op.f("ck_product_images_size_nonnegative"),
        ),
        sa.CheckConstraint(
            "width IS NULL OR width > 0",
            name=op.f("ck_product_images_width_positive"),
        ),
        sa.CheckConstraint(
            "height IS NULL OR height > 0",
            name=op.f("ck_product_images_height_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name=op.f("fk_product_images_product_id_products"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_images")),
        sa.UniqueConstraint("object_key", name=op.f("uq_product_images_object_key")),
    )
    op.create_index(
        "ix_product_images_product_type_sort",
        "product_images",
        ["product_id", "image_type", "sort_order"],
        unique=False,
    )
    op.create_index(
        "uq_product_images_one_cover",
        "product_images",
        ["product_id"],
        unique=True,
        postgresql_where=sa.text("image_type = 'cover'"),
        sqlite_where=sa.text("image_type = 'cover'"),
    )

    op.create_table(
        "import_jobs",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("workbook_sha256", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column(
            "dry_run",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("summary", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("errors", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'validated', 'completed', 'failed')",
            name=op.f("ck_import_jobs_status_allowed"),
        ),
        sa.CheckConstraint(
            "length(trim(original_filename)) BETWEEN 1 AND 255",
            name=op.f("ck_import_jobs_original_filename_length"),
        ),
        sa.CheckConstraint(
            "length(workbook_sha256) = 64",
            name=op.f("ck_import_jobs_workbook_sha256_length"),
        ),
        sa.CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) BETWEEN 8 AND 128",
            name=op.f("ck_import_jobs_idempotency_key_length"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_import_jobs_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )
    op.create_index(
        "ix_import_jobs_creator_created",
        "import_jobs",
        ["created_by", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_import_jobs_status_created",
        "import_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_import_jobs_workbook_sha256",
        "import_jobs",
        ["workbook_sha256"],
        unique=False,
    )
    op.create_index(
        "uq_import_jobs_creator_idempotency_key",
        "import_jobs",
        ["created_by", "idempotency_key"],
        unique=True,
    )

    op.create_table(
        "object_cleanup_jobs",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempts >= 0", name=op.f("ck_object_cleanup_jobs_attempts_nonnegative")
        ),
        sa.CheckConstraint(
            "length(trim(object_key)) BETWEEN 1 AND 512",
            name=op.f("ck_object_cleanup_jobs_object_key_length"),
        ),
        sa.CheckConstraint(
            "status IN ('intent', 'pending', 'processing', 'completed', 'failed')",
            name=op.f("ck_object_cleanup_jobs_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_object_cleanup_jobs_created_by_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_object_cleanup_jobs")),
    )
    op.create_index(
        "ix_object_cleanup_jobs_object_key",
        "object_cleanup_jobs",
        ["object_key"],
        unique=False,
    )
    op.create_index(
        "ix_object_cleanup_jobs_status_created",
        "object_cleanup_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_object_cleanup_jobs_creator_created",
        "object_cleanup_jobs",
        ["created_by", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("object_cleanup_jobs")
    op.drop_table("import_jobs")
    op.drop_table("product_images")
    op.drop_table("product_skus")
    op.drop_table("products")
    op.drop_table("categories")
    op.drop_column("users", "is_admin")
