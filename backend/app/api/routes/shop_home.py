from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.api.dependencies import (
    AdminUser,
    DbSession,
    client_key,
    require_same_origin_for_unsafe_request,
)
from app.core.errors import ApiError
from app.models import MiniCustomer
from app.schemas.catalog import ProductListResponse
from app.schemas.shop_home import (
    HotSearchResponse,
    ShopCustomerListResponse,
    ShopCustomerRead,
    ShopHomeResponse,
    ShopSearchRequest,
)
from app.services import shop_home


class _ShopRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        if not self.path.endswith("/search") or "POST" not in self.methods:
            return handler

        async def limited_search(request: Request) -> Response:
            retry_after = request.app.state.shop_search_rate_limiter.consume(client_key(request))
            if retry_after is not None:
                raise ApiError(
                    429,
                    "shop_search_rate_limited",
                    "搜索操作过于频繁，请稍后重试",
                    headers={"Retry-After": str(retry_after)},
                )
            return await handler(request)

        return limited_search


router = APIRouter(prefix="/shop", tags=["店铺首页"], route_class=_ShopRoute)
admin_router = APIRouter(
    prefix="/admin/shop",
    tags=["店铺客户选择"],
    dependencies=[Depends(require_same_origin_for_unsafe_request)],
)


@router.get("/home", response_model=ShopHomeResponse)
def home(session: DbSession) -> ShopHomeResponse:
    return ShopHomeResponse(data=shop_home.home(session))


@router.get("/hot-searches", response_model=HotSearchResponse)
def hot_searches(
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> HotSearchResponse:
    return HotSearchResponse(data=shop_home.hot_searches(session, limit=limit))


@router.post("/search", response_model=ProductListResponse)
def search(payload: ShopSearchRequest, session: DbSession) -> ProductListResponse:
    return ProductListResponse(data=shop_home.search_products(session, payload))


@admin_router.get("/customers", response_model=ShopCustomerListResponse)
def customers(
    _admin: AdminUser,
    request: Request,
    session: DbSession,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ShopCustomerListResponse:
    statement = select(MiniCustomer).where(MiniCustomer.is_active.is_(True))
    app_id = request.app.state.settings.wechat_miniprogram_app_id
    if app_id:
        statement = statement.where(MiniCustomer.app_id == app_id.strip())
    rows = session.scalars(
        statement.order_by(MiniCustomer.id).offset((page - 1) * page_size).limit(page_size)
    )
    return ShopCustomerListResponse(
        data=[ShopCustomerRead(id=customer.id, nickname=customer.nickname) for customer in rows]
    )
