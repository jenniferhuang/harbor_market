from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ShopProfile(Base):
    __tablename__ = "shop_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    name: Mapped[str] = mapped_column(
        String(160), nullable=False, default="港湾集市", server_default=text("'港湾集市'")
    )
    phone: Mapped[str] = mapped_column(
        String(40), nullable=False, default="", server_default=text("''")
    )
    address: Mapped[str] = mapped_column(
        String(500), nullable=False, default="", server_default=text("''")
    )
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    owner_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("mini_customers.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 160", name="name_length"),
        CheckConstraint("length(phone) <= 40", name="phone_length"),
        CheckConstraint("length(address) <= 500", name="address_length"),
        CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180", name="longitude_range"
        ),
    )


class ShopMedia(Base):
    __tablename__ = "shop_media"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(
        String(160), nullable=False, default="", server_default=text("''")
    )
    media_type: Mapped[str] = mapped_column(String(20), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('carousel', 'category', 'announcement')", name="kind_allowed"),
        CheckConstraint(
            "(kind = 'category' AND category_id IS NOT NULL) "
            "OR (kind IN ('carousel', 'announcement') AND category_id IS NULL)",
            name="category_scope",
        ),
        CheckConstraint("length(title) <= 160", name="title_length"),
        CheckConstraint("media_type IN ('image', 'video')", name="media_type_allowed"),
        CheckConstraint("kind = 'carousel' OR media_type = 'image'", name="noncarousel_image_only"),
        CheckConstraint("length(trim(object_key)) BETWEEN 1 AND 512", name="object_key_length"),
        CheckConstraint("length(trim(mime_type)) BETWEEN 1 AND 100", name="mime_type_length"),
        CheckConstraint("size_bytes > 0", name="size_positive"),
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        Index("ix_shop_media_kind_active_sort", "kind", "is_active", "sort_order"),
        Index(
            "uq_shop_media_one_active_category",
            "category_id",
            unique=True,
            postgresql_where=text("kind = 'category' AND is_active"),
            sqlite_where=text("kind = 'category' AND is_active = 1"),
        ),
        Index(
            "uq_shop_media_one_active_announcement",
            "kind",
            unique=True,
            postgresql_where=text("kind = 'announcement' AND is_active"),
            sqlite_where=text("kind = 'announcement' AND is_active = 1"),
        ),
    )


class ShopCoupon(Base):
    __tablename__ = "shop_coupons"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    min_spend_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    discount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 160", name="title_length"),
        CheckConstraint("min_spend_cents >= 0", name="min_spend_nonnegative"),
        CheckConstraint("discount_cents > 0", name="discount_positive"),
        CheckConstraint("discount_cents <= min_spend_cents", name="discount_not_above_minimum"),
        CheckConstraint("expires_at > starts_at", name="expiry_after_start"),
        Index("ix_shop_coupons_active_window", "is_active", "starts_at", "expires_at"),
    )


class MiniCouponClaim(Base):
    __tablename__ = "mini_coupon_claims"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("mini_customers.id", ondelete="RESTRICT"), nullable=False
    )
    coupon_id: Mapped[int] = mapped_column(
        ForeignKey("shop_coupons.id", ondelete="RESTRICT"), nullable=False
    )
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("customer_id", "coupon_id", name="uq_mini_coupon_claims_customer_coupon"),
        Index("ix_mini_coupon_claims_coupon", "coupon_id"),
    )


class MiniFavorite(Base):
    __tablename__ = "mini_favorites"

    customer_id: Mapped[int] = mapped_column(
        ForeignKey("mini_customers.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("ix_mini_favorites_product", "product_id"),)


class ShopOrder(Base):
    __tablename__ = "shop_orders"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    external_reference: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("mini_customers.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="completed", server_default=text("'completed'")
    )
    total_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    recorded_by_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    items: Mapped[list[ShopOrderLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        CheckConstraint(
            "length(trim(external_reference)) BETWEEN 1 AND 100", name="external_reference_length"
        ),
        CheckConstraint("status IN ('completed', 'voided')", name="status_allowed"),
        CheckConstraint("total_amount_cents >= 0", name="total_nonnegative"),
        CheckConstraint(
            "(status = 'completed' AND voided_at IS NULL) "
            "OR (status = 'voided' AND voided_at IS NOT NULL AND voided_at >= completed_at)",
            name="void_state",
        ),
        Index("ix_shop_orders_customer_status_completed", "customer_id", "status", "completed_at"),
        Index("ix_shop_orders_status_completed", "status", "completed_at"),
    )


class ShopOrderLine(Base):
    __tablename__ = "shop_order_lines"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("shop_orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"))
    product_code: Mapped[str] = mapped_column(String(64), nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    unit_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[ShopOrder] = relationship(back_populates="items")

    __table_args__ = (
        CheckConstraint("length(trim(product_code)) BETWEEN 1 AND 64", name="product_code_length"),
        CheckConstraint("length(trim(product_name)) BETWEEN 1 AND 160", name="product_name_length"),
        CheckConstraint("unit_price_cents >= 0", name="unit_price_nonnegative"),
        CheckConstraint("quantity BETWEEN 1 AND 100000", name="quantity_range"),
        CheckConstraint("line_total_cents >= 0", name="line_total_nonnegative"),
        CheckConstraint(
            "line_total_cents = unit_price_cents * quantity", name="line_total_matches_price"
        ),
        Index("ix_shop_order_lines_order", "order_id"),
        Index("ix_shop_order_lines_product", "product_id"),
    )
