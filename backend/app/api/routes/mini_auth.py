from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from fastapi.routing import APIRoute

from app.api.dependencies import DbSession, client_key
from app.core.errors import ApiError
from app.models import MiniCustomer, MiniSession
from app.schemas.auth import ErrorResponse
from app.schemas.mini_auth import (
    MiniCustomerPublic,
    MiniCustomerResponse,
    MiniLoginData,
    MiniLoginRequest,
    MiniLoginResponse,
    MiniLogoutData,
    MiniLogoutResponse,
    MiniProfileRequest,
)
from app.services.mini_avatar import prepare_avatar, read_avatar, replace_avatar


class _MiniAuthRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        if not self.path.endswith("/login") or "POST" not in self.methods:
            return handler

        async def limited_login(request: Request) -> Response:
            # Dependencies run after JSON decoding. Count even malformed bodies
            # before FastAPI parses them or performs schema validation.
            _limit_login(request)
            return await handler(request)

        return limited_login


router = APIRouter(
    prefix="/mini/auth",
    tags=["小程序微信登录"],
    route_class=_MiniAuthRoute,
    responses={
        401: {"model": ErrorResponse, "description": "请重新登录"},
        422: {"model": ErrorResponse, "description": "请求参数无效"},
        503: {"model": ErrorResponse, "description": "服务暂不可用"},
    },
)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"


def _public(customer: MiniCustomer) -> MiniCustomerPublic:
    return MiniCustomerPublic(
        id=customer.id,
        nickname=customer.nickname,
        avatar_url="/api/v1/mini/auth/avatar" if customer.avatar_object_key else None,
    )


def _authenticated(
    request: Request,
    session: DbSession,
) -> tuple[MiniCustomer, MiniSession]:
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    identity = None
    if separator and scheme.casefold() == "bearer" and token and token == token.strip():
        identity = request.app.state.mini_auth_service.authenticate(session, token)
    app_id = request.app.state.settings.wechat_miniprogram_app_id
    if identity is not None and app_id and identity[0].app_id != app_id.strip():
        identity = None
    if identity is None:
        raise ApiError(
            401,
            "mini_authentication_required",
            "登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer", "Cache-Control": "private, no-store"},
        )
    return identity


MiniAuthentication = Annotated[tuple[MiniCustomer, MiniSession], Depends(_authenticated)]


def _limit_login(request: Request) -> None:
    retry_after = request.app.state.mini_login_rate_limiter.consume(client_key(request))
    if retry_after is not None:
        raise ApiError(
            429,
            "mini_login_rate_limited",
            "登录操作过于频繁，请稍后重试",
            headers={"Retry-After": str(retry_after)},
        )


@router.post(
    "/login",
    response_model=MiniLoginResponse,
)
def login(
    payload: MiniLoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
) -> MiniLoginResponse:
    identity = request.app.state.wechat_auth_provider.exchange_code(payload.code)
    app_id = request.app.state.settings.wechat_miniprogram_app_id
    if app_id and identity.app_id != app_id.strip():
        raise ApiError(502, "mini_identity_invalid", "微信身份校验失败，请重新登录")
    customer, token, expires_at = request.app.state.mini_auth_service.login(
        session, identity, payload.code, request.app.state.settings.mini_session_ttl_seconds
    )
    _no_store(response)
    return MiniLoginResponse(
        data=MiniLoginData(access_token=token, expires_at=expires_at, customer=_public(customer))
    )


@router.get("/me", response_model=MiniCustomerResponse)
def me(identity: MiniAuthentication, response: Response) -> MiniCustomerResponse:
    _no_store(response)
    return MiniCustomerResponse(data=_public(identity[0]))


@router.patch("/profile", response_model=MiniCustomerResponse)
def profile(
    payload: MiniProfileRequest,
    identity: MiniAuthentication,
    request: Request,
    response: Response,
    session: DbSession,
) -> MiniCustomerResponse:
    customer = request.app.state.mini_auth_service.update_nickname(
        session, identity[0], payload.nickname
    )
    _no_store(response)
    return MiniCustomerResponse(data=_public(customer))


@router.post("/avatar", response_model=MiniCustomerResponse)
def upload_avatar(
    identity: MiniAuthentication,
    request: Request,
    response: Response,
    session: DbSession,
    file: Annotated[UploadFile, File()],
) -> MiniCustomerResponse:
    limit = request.app.state.settings.mini_avatar_upload_max_bytes
    payload = file.file.read(limit + 1)
    if len(payload) > limit:
        raise ApiError(413, "mini_avatar_too_large", "头像文件超过上传限制，请选择较小的图片")
    encoded = prepare_avatar(payload)
    customer = replace_avatar(session, request.app.state.object_storage, identity[0], encoded)
    _no_store(response)
    return MiniCustomerResponse(data=_public(customer))


@router.get("/avatar", response_class=Response)
def avatar(identity: MiniAuthentication, request: Request) -> Response:
    payload = read_avatar(request.app.state.object_storage, identity[0])
    response = Response(content=payload, media_type="image/jpeg")
    _no_store(response)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@router.post("/logout", response_model=MiniLogoutResponse)
def logout(
    identity: MiniAuthentication,
    request: Request,
    response: Response,
    session: DbSession,
) -> MiniLogoutResponse:
    request.app.state.mini_auth_service.logout(session, identity[1])
    _no_store(response)
    return MiniLogoutResponse(data=MiniLogoutData())
