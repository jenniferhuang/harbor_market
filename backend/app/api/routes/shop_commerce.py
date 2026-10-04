from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.dependencies import (
    AdminUser,
    DbSession,
    get_current_admin,
    require_same_origin_for_unsafe_request,
)
from app.api.routes.mini_auth import MiniAuthentication
from app.schemas.shop_commerce import (
    MAX_AMOUNT_CENTS,
    AdminCouponListResponse,
    AdminCouponResponse,
    CouponCreate,
    CouponListResponse,
    CouponResponse,
    CouponUpdate,
    EmptyRequest,
    FavoriteListResponse,
    FavoriteResponse,
    FavoriteState,
    HistoricalOrderCreate,
    OrderListResponse,
    OrderResponse,
)
from app.services.catalog import CatalogService
from app.services.shop_commerce import (
    ShopCommerceService,
    serialize_admin_coupon,
    serialize_order,
)

mini_router = APIRouter(prefix="/mini/shop", tags=["小程序商城记录"])
admin_router = APIRouter(
    prefix="/admin/shop",
    tags=["商城记录管理"],
    dependencies=[Depends(get_current_admin), Depends(require_same_origin_for_unsafe_request)],
)
commerce = ShopCommerceService()
catalog = CatalogService()
Page = Annotated[int, Query(ge=1, le=1_000_000)]
PageSize = Annotated[int, Query(ge=1, le=100)]
EntityId = Annotated[int, Path(ge=1, le=MAX_AMOUNT_CENTS)]
ProductCode = Annotated[str, Path(min_length=1, max_length=64)]


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"


@mini_router.get("/favorites", response_model=FavoriteListResponse)
def list_favorites(
    identity: MiniAuthentication,
    session: DbSession,
    response: Response,
    page: Page = 1,
    page_size: PageSize = 20,
) -> FavoriteListResponse:
    _private(response)
    products = commerce.list_favorites(session, identity[0].id, page=page, page_size=page_size)
    return FavoriteListResponse(
        data=[catalog.serialize_product(product, public_only=True) for product in products]
    )


@mini_router.get("/favorites/{product_code}", response_model=FavoriteResponse)
def favorite_state(
    product_code: ProductCode, identity: MiniAuthentication, session: DbSession, response: Response
) -> FavoriteResponse:
    _private(response)
    return FavoriteResponse(
        data=FavoriteState(
            is_favorite=commerce.favorite_state(session, identity[0].id, product_code)
        )
    )


@mini_router.put("/favorites/{product_code}", response_model=FavoriteResponse)
def add_favorite(
    product_code: ProductCode,
    identity: MiniAuthentication,
    session: DbSession,
    response: Response,
    payload: EmptyRequest | None = None,
) -> FavoriteResponse:
    _private(response)
    return FavoriteResponse(
        data=FavoriteState(
            is_favorite=commerce.set_favorite(
                session, identity[0].id, product_code, is_favorite=True
            )
        )
    )


@mini_router.delete("/favorites/{product_code}", response_model=FavoriteResponse)
def remove_favorite(
    product_code: ProductCode,
    identity: MiniAuthentication,
    session: DbSession,
    response: Response,
    payload: EmptyRequest | None = None,
) -> FavoriteResponse:
    _private(response)
    return FavoriteResponse(
        data=FavoriteState(
            is_favorite=commerce.set_favorite(
                session, identity[0].id, product_code, is_favorite=False
            )
        )
    )


@mini_router.get("/coupons", response_model=CouponListResponse)
def list_customer_coupons(
    identity: MiniAuthentication,
    session: DbSession,
    response: Response,
    page: Page = 1,
    page_size: PageSize = 20,
) -> CouponListResponse:
    _private(response)
    return CouponListResponse(
        data=commerce.list_customer_coupons(session, identity[0].id, page=page, page_size=page_size)
    )


@mini_router.post("/coupons/{coupon_id}/claim", response_model=CouponResponse)
def claim_coupon(
    coupon_id: EntityId,
    identity: MiniAuthentication,
    session: DbSession,
    response: Response,
    payload: EmptyRequest | None = None,
) -> CouponResponse:
    _private(response)
    return CouponResponse(data=commerce.claim_coupon(session, identity[0].id, coupon_id))


@mini_router.get("/orders", response_model=OrderListResponse)
def list_customer_orders(
    identity: MiniAuthentication,
    session: DbSession,
    response: Response,
    page: Page = 1,
    page_size: PageSize = 20,
) -> OrderListResponse:
    _private(response)
    return OrderListResponse(
        data=[
            serialize_order(order)
            for order in commerce.list_orders(
                session, customer_id=identity[0].id, page=page, page_size=page_size
            )
        ]
    )


@admin_router.get("/coupons", response_model=AdminCouponListResponse)
def list_admin_coupons(
    session: DbSession, page: Page = 1, page_size: PageSize = 20
) -> AdminCouponListResponse:
    return AdminCouponListResponse(
        data=[
            serialize_admin_coupon(coupon)
            for coupon in commerce.list_coupons(session, page=page, page_size=page_size)
        ]
    )


@admin_router.post("/coupons", response_model=AdminCouponResponse, status_code=201)
def create_coupon(payload: CouponCreate, session: DbSession) -> AdminCouponResponse:
    return AdminCouponResponse(
        data=serialize_admin_coupon(commerce.create_coupon(session, payload))
    )


@admin_router.patch("/coupons/{coupon_id}", response_model=AdminCouponResponse)
def update_coupon(
    coupon_id: EntityId, payload: CouponUpdate, session: DbSession
) -> AdminCouponResponse:
    return AdminCouponResponse(
        data=serialize_admin_coupon(commerce.update_coupon(session, coupon_id, payload))
    )


@admin_router.get("/orders", response_model=OrderListResponse)
def list_admin_orders(
    session: DbSession,
    page: Page = 1,
    page_size: PageSize = 20,
    customer_id: Annotated[int | None, Query(ge=1, le=MAX_AMOUNT_CENTS)] = None,
) -> OrderListResponse:
    return OrderListResponse(
        data=[
            serialize_order(order)
            for order in commerce.list_orders(
                session, page=page, page_size=page_size, customer_id=customer_id
            )
        ]
    )


@admin_router.post("/orders", response_model=OrderResponse)
def record_order(
    payload: HistoricalOrderCreate, session: DbSession, admin: AdminUser
) -> OrderResponse:
    """记录已在线下完成的历史订单，并保存商品和价格快照。"""
    return OrderResponse(
        data=serialize_order(commerce.record_order(session, payload, recorded_by_user_id=admin.id))
    )


@admin_router.post("/orders/{order_id}/void", response_model=OrderResponse)
def void_order(
    order_id: EntityId, session: DbSession, payload: EmptyRequest | None = None
) -> OrderResponse:
    return OrderResponse(data=serialize_order(commerce.void_order(session, order_id)))
