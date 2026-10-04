from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Select, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models import Category, Product, ShopCoupon, ShopMedia, ShopOrder, ShopOrderLine
from app.schemas.catalog import ProductListData
from app.schemas.shop_home import (
    HomeCarousel,
    HomeCategory,
    HomeCoupon,
    HotProduct,
    HotSearch,
    ShopHomeData,
    ShopSearchRequest,
)
from app.schemas.shop_store import StoreRead
from app.services.catalog import CatalogService, product_search_condition
from app.services.shop_commerce import as_utc
from app.services.shop_store import public_store

catalog = CatalogService()


def _public_products() -> Select[tuple[Product]]:
    return (
        select(Product)
        .join(Product.category)
        .where(Product.status == "published", Category.is_active.is_(True))
        .options(
            selectinload(Product.category),
            selectinload(Product.skus),
            selectinload(Product.images),
        )
    )


def search_products(session: Session, payload: ShopSearchRequest) -> ProductListData:
    # One database update counts every matching public product once, independently
    # of result pagination; arithmetic in SQL preserves concurrent increments.
    matched_ids = (
        select(Product.id)
        .join(Product.category)
        .where(
            Product.status == "published",
            Category.is_active.is_(True),
            product_search_condition(payload.q),
        )
    )
    session.execute(
        update(Product)
        .where(Product.id.in_(matched_ids))
        .values(search_hit_count=Product.search_hit_count + 1, updated_at=Product.updated_at)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    products, total = catalog.list_products(
        session,
        page=payload.page,
        page_size=payload.page_size,
        query_text=payload.q,
        public_only=True,
    )
    return ProductListData(
        items=[catalog.serialize_product(product, public_only=True) for product in products],
        total=total,
        page=payload.page,
        page_size=payload.page_size,
    )


def hot_searches(session: Session, *, limit: int = 10) -> list[HotSearch]:
    products = session.scalars(
        _public_products()
        .where(Product.search_hit_count > 0)
        .order_by(Product.search_hit_count.desc(), Product.sort_order, Product.id)
        .limit(limit)
    )
    return [
        HotSearch(
            product=catalog.serialize_product(product, public_only=True),
            search_hit_count=product.search_hit_count,
        )
        for product in products
    ]


def hot_products(
    session: Session, *, limit: int = 12
) -> tuple[list[HotProduct], Literal["sales", "featured", "newest"]]:
    sales = (
        select(
            ShopOrderLine.product_id.label("product_id"),
            func.sum(ShopOrderLine.quantity).label("sold_quantity"),
            func.count(func.distinct(ShopOrder.id)).label("order_count"),
        )
        .join(ShopOrder, ShopOrder.id == ShopOrderLine.order_id)
        .where(ShopOrder.status == "completed")
        .group_by(ShopOrderLine.product_id)
        .subquery()
    )
    customer_purchases = (
        select(
            ShopOrderLine.product_id.label("product_id"),
            ShopOrder.customer_id,
            func.count(func.distinct(ShopOrder.id)).label("purchase_count"),
        )
        .join(ShopOrder, ShopOrder.id == ShopOrderLine.order_id)
        .where(ShopOrder.status == "completed", ShopOrder.customer_id.is_not(None))
        .group_by(ShopOrderLine.product_id, ShopOrder.customer_id)
        .subquery()
    )
    repeats = (
        select(
            customer_purchases.c.product_id,
            func.sum(customer_purchases.c.purchase_count - 1).label("repeat_purchase_count"),
        )
        .where(customer_purchases.c.purchase_count > 1)
        .group_by(customer_purchases.c.product_id)
        .subquery()
    )
    rows = session.execute(
        _public_products()
        .join(sales, sales.c.product_id == Product.id)
        .outerjoin(repeats, repeats.c.product_id == Product.id)
        .add_columns(
            sales.c.sold_quantity,
            sales.c.order_count,
            func.coalesce(repeats.c.repeat_purchase_count, 0),
        )
        .order_by(sales.c.sold_quantity.desc(), sales.c.order_count.desc(), Product.id)
        .limit(limit)
    ).all()
    if rows:
        return [
            HotProduct(
                product=catalog.serialize_product(product, public_only=True),
                sold_quantity=int(quantity),
                order_count=int(order_count),
                repeat_purchase_count=int(repeat_count),
            )
            for product, quantity, order_count, repeat_count in rows
        ], "sales"

    recommendations = list(
        session.scalars(
            _public_products()
            .where(Product.featured.is_(True))
            .order_by(Product.sort_order, Product.id)
            .limit(limit)
        )
    )
    source: Literal["sales", "featured", "newest"] = "featured"
    if not recommendations:
        source = "newest"
        recommendations = list(
            session.scalars(
                _public_products()
                .order_by(Product.created_at.desc(), Product.id.desc())
                .limit(limit)
            )
        )
    return [
        HotProduct(
            product=catalog.serialize_product(product, public_only=True),
            sold_quantity=0,
            order_count=0,
            repeat_purchase_count=0,
        )
        for product in recommendations
    ], source


def home(session: Session) -> ShopHomeData:
    now = datetime.now(UTC)
    carousel = list(
        session.scalars(
            select(ShopMedia)
            .where(ShopMedia.kind == "carousel", ShopMedia.is_active.is_(True))
            .order_by(ShopMedia.sort_order, ShopMedia.id)
            .limit(20)
        )
    )
    category_images = dict(
        session.execute(
            select(ShopMedia.category_id, ShopMedia.id).where(
                ShopMedia.kind == "category", ShopMedia.is_active.is_(True)
            )
        ).all()
    )
    categories = session.scalars(
        select(Category)
        .where(Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name, Category.id)
        .limit(100)
    )
    coupons = session.scalars(
        select(ShopCoupon)
        .where(
            ShopCoupon.is_active.is_(True),
            ShopCoupon.starts_at <= now,
            ShopCoupon.expires_at > now,
        )
        .order_by(ShopCoupon.discount_cents.desc(), ShopCoupon.id)
        .limit(20)
    )
    products, source = hot_products(session)
    return ShopHomeData(
        store=StoreRead.model_validate(public_store(session)),
        carousel=[
            HomeCarousel(
                id=media.id,
                title=media.title,
                media_type=media.media_type,
                url=f"/api/v1/shop/media/{media.id}",
            )
            for media in carousel
        ],
        categories=[
            HomeCategory(
                id=category.id,
                code=category.code,
                name=category.name,
                image_url=f"/api/v1/shop/media/{category_images[category.id]}"
                if category.id in category_images
                else None,
            )
            for category in categories
        ],
        coupons=[
            HomeCoupon(
                id=coupon.id,
                title=coupon.title,
                min_spend_cents=coupon.min_spend_cents,
                discount_cents=coupon.discount_cents,
                starts_at=as_utc(coupon.starts_at),
                expires_at=as_utc(coupon.expires_at),
            )
            for coupon in coupons
        ],
        hot_products=products,
        hot_products_source=source,
    )
