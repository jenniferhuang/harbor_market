from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.errors import ApiError
from app.models import Category, MiniCouponClaim, Product, ProductSku, ShopCoupon
from app.schemas.catalog import ProductSpecification
from app.schemas.shop_cart import (
    CartCouponEvaluation,
    CartQuoteData,
    CartQuoteItemRead,
    CartQuoteItemRequest,
    CartQuoteRequest,
)
from app.schemas.shop_commerce import MAX_AMOUNT_CENTS
from app.services.shop_commerce import as_utc

MAX_COUPON_EVALUATIONS = 100


def _invalid_item(message: str) -> ApiError:
    return ApiError(422, "cart_item_unavailable", message)


def _resolve_sku(
    product: Product, item: CartQuoteItemRequest
) -> tuple[ProductSku, list[ProductSpecification]]:
    try:
        specifications = [
            ProductSpecification.model_validate(specification)
            for specification in product.specifications
        ]
    except ValidationError as exc:
        raise _invalid_item("商品规格已变化，请重新选择商品") from exc
    known_codes = {specification.code for specification in specifications}
    if item.selections.keys() - known_codes:
        raise _invalid_item("商品规格已变化，请重新选择商品")
    for specification in specifications:
        selected = item.selections.get(specification.code, [])
        known_options = {option.code for option in specification.options}
        maximum = 1 if specification.selection_mode == "single" else specification.max_select
        if (
            set(selected) - known_options
            or len(selected) < specification.min_select
            or len(selected) > maximum
        ):
            raise _invalid_item("商品规格选项无效，请重新选择")

    # Match the mini-program catalog resolver, including sparse SKU dimensions.
    skus = sorted(
        (sku for sku in product.skus if sku.is_active),
        key=lambda sku: (not sku.is_default, sku.sort_order, sku.id),
    )
    variant_codes = {code for sku in skus for code in sku.attributes}
    for sku in skus:
        if all(
            item.selections.get(code, []) == [sku.attributes[code]]
            if code in sku.attributes
            else not item.selections.get(code, [])
            for code in variant_codes
        ):
            if sku.sku_code != item.sku_code:
                raise _invalid_item("商品规格已变化，请重新选择商品")
            return sku, specifications
    raise _invalid_item("商品规格不存在或已停用，请重新选择")


def _unit_price(
    sku: ProductSku,
    specifications: list[ProductSpecification],
    selections: dict[str, list[str]],
) -> int:
    amount = sku.price_cents
    for specification in specifications:
        if specification.code in sku.attributes:
            continue
        selected = selections.get(specification.code, [])
        amount += sum(
            option.price_delta_cents for option in specification.options if option.code in selected
        )
    amount = max(0, amount)
    if amount > MAX_AMOUNT_CENTS:
        raise ApiError(422, "cart_amount_exceeded", "购物车金额超过允许范围")
    return amount


def _evaluate_coupon(
    coupon: ShopCoupon, *, subtotal_cents: int, now: datetime
) -> CartCouponEvaluation:
    shortfall = max(0, coupon.min_spend_cents - subtotal_cents)
    if not coupon.is_active:
        reason = "inactive"
    elif as_utc(coupon.starts_at) > now:
        reason = "not_started"
    elif as_utc(coupon.expires_at) <= now:
        reason = "expired"
    elif shortfall:
        reason = "below_minimum"
    else:
        reason = "eligible"
    return CartCouponEvaluation(
        coupon_id=coupon.id,
        title=coupon.title,
        min_spend_cents=coupon.min_spend_cents,
        discount_cents=coupon.discount_cents,
        eligible=reason == "eligible",
        shortfall_cents=shortfall,
        reason=reason,
    )


def quote_cart(session: Session, customer_id: int, payload: CartQuoteRequest) -> CartQuoteData:
    """Read current prices and claims without reserving stock, redeeming, or creating orders."""
    products = {
        product.product_code: product
        for product in session.scalars(
            select(Product)
            .join(Product.category)
            .where(
                Product.product_code.in_({item.product_code for item in payload.items}),
                Product.status == "published",
                Category.is_active.is_(True),
            )
            .options(selectinload(Product.skus))
        )
    }
    lines = []
    quantities: dict[int, int] = defaultdict(int)
    subtotal = 0
    for item in payload.items:
        product = products.get(item.product_code)
        if product is None or product.stock_status == "out_of_stock":
            raise _invalid_item("商品不存在、暂未上架或已售罄，请更新购物车")
        sku, specifications = _resolve_sku(product, item)
        quantities[sku.id] += item.quantity
        if quantities[sku.id] > sku.stock_quantity:
            raise ApiError(422, "cart_stock_insufficient", "商品库存不足，请调整购物车数量")
        unit_price = _unit_price(sku, specifications, item.selections)
        line_total = unit_price * item.quantity
        subtotal += line_total
        if subtotal > MAX_AMOUNT_CENTS:
            raise ApiError(422, "cart_amount_exceeded", "购物车金额超过允许范围")
        lines.append(
            CartQuoteItemRead(
                product_code=product.product_code,
                sku_code=sku.sku_code,
                name=product.name,
                sku_name=sku.name,
                selections=item.selections,
                quantity=item.quantity,
                unit_price_cents=unit_price,
                line_total_cents=line_total,
            )
        )

    owned_statement = (
        select(ShopCoupon)
        .join(MiniCouponClaim, MiniCouponClaim.coupon_id == ShopCoupon.id)
        .where(MiniCouponClaim.customer_id == customer_id)
    )
    coupons = list(
        session.scalars(
            owned_statement.order_by(
                MiniCouponClaim.claimed_at.desc(), MiniCouponClaim.id.desc()
            ).limit(MAX_COUPON_EVALUATIONS)
        )
    )
    if payload.coupon_id is not None and all(coupon.id != payload.coupon_id for coupon in coupons):
        selected = session.scalar(owned_statement.where(ShopCoupon.id == payload.coupon_id))
        if selected is None:
            raise ApiError(422, "cart_coupon_invalid", "请先领取所选优惠券")
        coupons = coupons[: MAX_COUPON_EVALUATIONS - 1] + [selected]

    now = datetime.now(UTC)
    evaluations = [_evaluate_coupon(coupon, subtotal_cents=subtotal, now=now) for coupon in coupons]
    discount = 0
    if payload.coupon_id is not None:
        selected_evaluation = next(
            evaluation for evaluation in evaluations if evaluation.coupon_id == payload.coupon_id
        )
        if not selected_evaluation.eligible:
            message = {
                "inactive": "所选优惠券已停用，请重新选择",
                "not_started": "所选优惠券尚未开始使用",
                "expired": "所选优惠券已过期，请重新选择",
                "below_minimum": "购物车金额未达到所选优惠券的满减门槛",
            }[selected_evaluation.reason]
            raise ApiError(422, "cart_coupon_ineligible", message)
        discount = selected_evaluation.discount_cents
    return CartQuoteData(
        items=lines,
        subtotal_cents=subtotal,
        discount_cents=discount,
        total_cents=subtotal - discount,
        coupon_id=payload.coupon_id,
        coupon_evaluations=evaluations,
    )
