from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.category import Category


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    product_code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(240))
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="draft",
        server_default=text("'draft'"),
    )
    base_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    market_price_cents: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="CNY",
        server_default=text("'CNY'"),
    )
    unit: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="件",
        server_default=text("'件'"),
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        server_default=text("''"),
    )
    featured: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    stock_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="in_stock",
        server_default=text("'in_stock'"),
    )
    inventory_count: Mapped[int | None] = mapped_column(Integer)
    tags: Mapped[list[Any]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    selling_points: Mapped[list[Any]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    specifications: Mapped[list[Any]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    ingredients: Mapped[str | None] = mapped_column(Text)
    allergen_info: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    category: Mapped[Category] = relationship(back_populates="products")
    skus: Mapped[list[ProductSku]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    images: Mapped[list[ProductImage]] = relationship(
        back_populates="product",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "length(trim(product_code)) BETWEEN 1 AND 64",
            name="product_code_length",
        ),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 160", name="name_length"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="status_allowed",
        ),
        CheckConstraint("base_price_cents >= 0", name="base_price_nonnegative"),
        CheckConstraint(
            "market_price_cents IS NULL OR market_price_cents >= base_price_cents",
            name="market_price_not_below_base",
        ),
        CheckConstraint("length(trim(unit)) BETWEEN 1 AND 20", name="unit_length"),
        CheckConstraint(
            "stock_status IN ('in_stock', 'out_of_stock', 'preorder')",
            name="stock_status_allowed",
        ),
        CheckConstraint(
            "inventory_count IS NULL OR inventory_count >= 0",
            name="inventory_count_nonnegative",
        ),
        Index("ix_products_category_status_sort", "category_id", "status", "sort_order"),
        Index("ix_products_status_featured_sort", "status", "featured", "sort_order"),
    )


class ProductSku(Base):
    __tablename__ = "product_skus"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    sku_code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    market_price_cents: Mapped[int | None] = mapped_column(Integer)
    stock_quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    product: Mapped[Product] = relationship(back_populates="skus")

    __table_args__ = (
        CheckConstraint("length(trim(sku_code)) BETWEEN 1 AND 80", name="sku_code_length"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 160", name="name_length"),
        CheckConstraint("price_cents >= 0", name="price_nonnegative"),
        CheckConstraint(
            "market_price_cents IS NULL OR market_price_cents >= price_cents",
            name="market_price_not_below_price",
        ),
        CheckConstraint("stock_quantity >= 0", name="stock_quantity_nonnegative"),
        Index("ix_product_skus_product_active_sort", "product_id", "is_active", "sort_order"),
        Index(
            "uq_product_skus_one_active_default",
            "product_id",
            unique=True,
            postgresql_where=text("is_default AND is_active"),
            sqlite_where=text("is_default = 1 AND is_active = 1"),
        ),
    )


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    image_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="gallery",
        server_default=text("'gallery'"),
    )
    alt_text: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=text("0"),
    )
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    product: Mapped[Product] = relationship(back_populates="images")

    __table_args__ = (
        CheckConstraint(
            "length(trim(object_key)) BETWEEN 1 AND 512",
            name="object_key_length",
        ),
        CheckConstraint(
            "image_type IN ('cover', 'gallery', 'detail')",
            name="image_type_allowed",
        ),
        CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("width IS NULL OR width > 0", name="width_positive"),
        CheckConstraint("height IS NULL OR height > 0", name="height_positive"),
        Index(
            "uq_product_images_one_cover",
            "product_id",
            unique=True,
            postgresql_where=text("image_type = 'cover'"),
            sqlite_where=text("image_type = 'cover'"),
        ),
        Index("ix_product_images_product_type_sort", "product_id", "image_type", "sort_order"),
    )
