from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import and_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.sql.elements import ColumnElement

from app.core.errors import ApiError
from app.models import (
    Category,
    MiniCouponClaim,
    MiniCustomer,
    MiniFavorite,
    Product,
    ShopCoupon,
    ShopOrder,
    ShopOrderLine,
)
from app.schemas.shop_commerce import (
    MAX_AMOUNT_CENTS,
    AdminCouponRead,
    CouponCreate,
    CouponRead,
    CouponUpdate,
    HistoricalOrderCreate,
    OrderItemRead,
    OrderRead,
)


def as_utc(value: datetime) -> datetime:
    # SQLite returns naive timestamps even for DateTime(timezone=True).
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def active_coupon_condition(now: datetime) -> ColumnElement[bool]:
    return and_(
        ShopCoupon.is_active.is_(True),
        ShopCoupon.starts_at <= now,
        ShopCoupon.expires_at > now,
    )


def serialize_coupon(coupon: ShopCoupon, claimed_at: datetime | None = None) -> CouponRead:
    return CouponRead(
        coupon_id=coupon.id,
        title=coupon.title,
        min_spend_cents=coupon.min_spend_cents,
        discount_cents=coupon.discount_cents,
        starts_at=as_utc(coupon.starts_at),
        expires_at=as_utc(coupon.expires_at),
        is_active=coupon.is_active,
        claimed_at=as_utc(claimed_at) if claimed_at is not None else None,
    )


def serialize_admin_coupon(coupon: ShopCoupon) -> AdminCouponRead:
    values = serialize_coupon(coupon).model_dump(exclude={"coupon_id", "claimed_at"})
    return AdminCouponRead(
        id=coupon.id,
        **values,
        created_at=as_utc(coupon.created_at),
        updated_at=as_utc(coupon.updated_at),
    )


def serialize_order(order: ShopOrder) -> OrderRead:
    return OrderRead(
        id=order.id,
        external_reference=order.external_reference,
        customer_id=order.customer_id,
        status=order.status,
        total_cents=order.total_amount_cents,
        completed_at=as_utc(order.completed_at),
        items=[
            OrderItemRead(
                product_code=item.product_code,
                product_name=item.product_name,
                unit_price_cents=item.unit_price_cents,
                quantity=item.quantity,
                line_total_cents=item.line_total_cents,
            )
            for item in sorted(order.items, key=lambda item: item.id)
        ],
    )


class ShopCommerceService:
    def list_favorites(
        self, session: Session, customer_id: int, *, page: int, page_size: int
    ) -> list[Product]:
        return list(
            session.scalars(
                select(Product)
                .join(MiniFavorite, MiniFavorite.product_id == Product.id)
                .join(Product.category)
                .where(
                    MiniFavorite.customer_id == customer_id,
                    Product.status == "published",
                    Category.is_active.is_(True),
                )
                .options(
                    selectinload(Product.category),
                    selectinload(Product.skus),
                    selectinload(Product.images),
                )
                .order_by(MiniFavorite.created_at.desc(), Product.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )

    @staticmethod
    def _favorite_product(session: Session, product_code: str) -> Product:
        product = session.scalar(
            select(Product)
            .join(Product.category)
            .where(
                Product.product_code == product_code.strip().upper(),
                Product.status == "published",
                Category.is_active.is_(True),
            )
        )
        if product is None:
            raise ApiError(404, "product_not_found", "商品不存在或暂未上架")
        return product

    def favorite_state(self, session: Session, customer_id: int, product_code: str) -> bool:
        product = self._favorite_product(session, product_code)
        return session.get(MiniFavorite, (customer_id, product.id)) is not None

    def set_favorite(
        self, session: Session, customer_id: int, product_code: str, *, is_favorite: bool
    ) -> bool:
        product = self._favorite_product(session, product_code)
        identity = (customer_id, product.id)
        existing = session.get(MiniFavorite, identity)
        if is_favorite and existing is None:
            session.add(MiniFavorite(customer_id=customer_id, product_id=product.id))
        elif not is_favorite and existing is not None:
            session.delete(existing)
        else:
            return is_favorite
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if is_favorite and session.get(MiniFavorite, identity) is not None:
                return True
            raise ApiError(409, "favorite_conflict", "收藏状态发生变化，请重试") from exc
        return is_favorite

    def list_customer_coupons(
        self,
        session: Session,
        customer_id: int,
        *,
        page: int,
        page_size: int,
        now: datetime | None = None,
    ) -> list[CouponRead]:
        rows = session.execute(
            select(ShopCoupon, MiniCouponClaim.claimed_at)
            .outerjoin(
                MiniCouponClaim,
                and_(
                    MiniCouponClaim.coupon_id == ShopCoupon.id,
                    MiniCouponClaim.customer_id == customer_id,
                ),
            )
            .where(active_coupon_condition(now or datetime.now(UTC)))
            .order_by(ShopCoupon.expires_at, ShopCoupon.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return [serialize_coupon(coupon, claimed_at) for coupon, claimed_at in rows]

    def claim_coupon(self, session: Session, customer_id: int, coupon_id: int) -> CouponRead:
        # Serialize claims against admin edits to the coupon validity window.
        coupon = session.scalar(
            select(ShopCoupon).where(ShopCoupon.id == coupon_id).with_for_update()
        )
        if coupon is None:
            raise ApiError(404, "coupon_unavailable", "优惠券不存在或不在领取时间内")
        claim_statement = select(MiniCouponClaim).where(
            MiniCouponClaim.customer_id == customer_id,
            MiniCouponClaim.coupon_id == coupon_id,
        )
        existing = session.scalar(claim_statement)
        if existing is not None:
            return serialize_coupon(coupon, existing.claimed_at)
        now = datetime.now(UTC)
        if (
            not coupon.is_active
            or as_utc(coupon.starts_at) > now
            or as_utc(coupon.expires_at) <= now
        ):
            raise ApiError(404, "coupon_unavailable", "优惠券不存在或不在领取时间内")
        claim = MiniCouponClaim(customer_id=customer_id, coupon_id=coupon_id, claimed_at=now)
        session.add(claim)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            existing = session.scalar(claim_statement)
            if existing is None:
                raise ApiError(
                    409, "coupon_claim_conflict", "优惠券领取状态发生变化，请重试"
                ) from exc
            return serialize_coupon(coupon, existing.claimed_at)
        return serialize_coupon(coupon, claim.claimed_at)

    @staticmethod
    def list_coupons(session: Session, *, page: int, page_size: int) -> list[ShopCoupon]:
        return list(
            session.scalars(
                select(ShopCoupon)
                .order_by(ShopCoupon.created_at.desc(), ShopCoupon.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )

    @staticmethod
    def create_coupon(session: Session, payload: CouponCreate) -> ShopCoupon:
        coupon = ShopCoupon(**payload.model_dump())
        session.add(coupon)
        session.commit()
        return coupon

    @staticmethod
    def update_coupon(session: Session, coupon_id: int, payload: CouponUpdate) -> ShopCoupon:
        coupon = session.scalar(
            select(ShopCoupon).where(ShopCoupon.id == coupon_id).with_for_update()
        )
        if coupon is None:
            raise ApiError(404, "coupon_not_found", "优惠券不存在")
        changes = payload.model_dump(exclude_unset=True)
        current = {
            name: as_utc(getattr(coupon, name))
            if name in {"starts_at", "expires_at"}
            else getattr(coupon, name)
            for name in CouponCreate.model_fields
        }
        try:
            validated = CouponCreate.model_validate({**current, **changes})
        except ValidationError as exc:
            raise ApiError(422, "invalid_coupon", "优惠券金额或有效期无效，请检查后重试") from exc
        for name, value in validated.model_dump().items():
            setattr(coupon, name, value)
        session.commit()
        return coupon

    @staticmethod
    def list_orders(
        session: Session,
        *,
        page: int,
        page_size: int,
        customer_id: int | None = None,
    ) -> list[ShopOrder]:
        statement = select(ShopOrder).options(selectinload(ShopOrder.items))
        if customer_id is not None:
            statement = statement.where(ShopOrder.customer_id == customer_id)
        return list(
            session.scalars(
                statement.order_by(ShopOrder.completed_at.desc(), ShopOrder.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )

    @staticmethod
    def _existing_order(session: Session, external_reference: str) -> ShopOrder | None:
        return session.scalar(
            select(ShopOrder)
            .where(ShopOrder.external_reference == external_reference)
            .options(selectinload(ShopOrder.items))
        )

    @staticmethod
    def _ensure_same_order(order: ShopOrder, payload: HistoricalOrderCreate) -> ShopOrder:
        stored_items = sorted(
            (item.product_code, item.quantity, item.unit_price_cents) for item in order.items
        )
        requested_items = sorted(
            (item.product_code, item.quantity, item.unit_price_cents) for item in payload.items
        )
        if (
            order.customer_id != payload.customer_id
            or (
                payload.completed_at is not None
                and as_utc(order.completed_at) != payload.completed_at
            )
            or stored_items != requested_items
        ):
            raise ApiError(409, "order_reference_conflict", "外部订单编号已用于不同的订单记录")
        return order

    def record_order(
        self, session: Session, payload: HistoricalOrderCreate, *, recorded_by_user_id: int
    ) -> ShopOrder:
        existing = self._existing_order(session, payload.external_reference)
        if existing is not None:
            return self._ensure_same_order(existing, payload)
        completed_at = payload.completed_at or datetime.now(UTC)
        if completed_at > datetime.now(UTC) + timedelta(seconds=60):
            raise ApiError(422, "order_time_invalid", "历史订单的完成时间不能晚于当前时间")
        if (
            payload.customer_id is not None
            and session.get(MiniCustomer, payload.customer_id) is None
        ):
            raise ApiError(404, "customer_not_found", "顾客不存在")
        total = sum(item.quantity * item.unit_price_cents for item in payload.items)
        if total > MAX_AMOUNT_CENTS:
            raise ApiError(422, "order_amount_invalid", "订单金额超过允许范围")
        codes = {item.product_code for item in payload.items}
        products = {
            product.product_code: product
            for product in session.scalars(select(Product).where(Product.product_code.in_(codes)))
        }
        if set(products) != codes:
            raise ApiError(404, "product_not_found", "订单包含不存在的商品")
        order = ShopOrder(
            external_reference=payload.external_reference,
            customer_id=payload.customer_id,
            status="completed",
            total_amount_cents=total,
            completed_at=completed_at,
            recorded_by_user_id=recorded_by_user_id,
            items=[
                ShopOrderLine(
                    product_id=products[item.product_code].id,
                    product_code=products[item.product_code].product_code,
                    product_name=products[item.product_code].name,
                    unit_price_cents=item.unit_price_cents,
                    quantity=item.quantity,
                    line_total_cents=item.quantity * item.unit_price_cents,
                )
                for item in payload.items
            ],
        )
        session.add(order)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            existing = self._existing_order(session, payload.external_reference)
            if existing is not None:
                return self._ensure_same_order(existing, payload)
            raise ApiError(409, "order_record_conflict", "订单记录发生冲突，请重试") from exc
        return order

    @staticmethod
    def void_order(session: Session, order_id: int) -> ShopOrder:
        order = session.scalar(
            select(ShopOrder)
            .where(ShopOrder.id == order_id)
            .options(selectinload(ShopOrder.items))
            .with_for_update()
        )
        if order is None:
            raise ApiError(404, "order_not_found", "订单记录不存在")
        if order.status != "voided":
            order.status = "voided"
            order.voided_at = max(datetime.now(UTC), as_utc(order.completed_at))
            session.commit()
        return order
