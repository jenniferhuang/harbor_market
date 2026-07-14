from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.dependencies import (
    AuthServiceDependency,
    CurrentUser,
    DbSession,
    client_key,
    enforce_registration_rate_limit,
    rate_limit_error,
)
from app.core.errors import ApiError
from app.schemas.auth import (
    ErrorResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    UserPublic,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])

AUTH_ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "Authentication failed or is required"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
}


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {"model": ErrorResponse, "description": "Username is unavailable"},
        422: AUTH_ERROR_RESPONSES[422],
        429: AUTH_ERROR_RESPONSES[429],
    },
    dependencies=[Depends(enforce_registration_rate_limit)],
)
def register(
    payload: RegisterRequest,
    session: DbSession,
    auth_service: AuthServiceDependency,
) -> UserResponse:
    user = auth_service.register(session, payload)
    return UserResponse(data=UserPublic.model_validate(user))


@router.post(
    "/login",
    response_model=UserResponse,
    responses=AUTH_ERROR_RESPONSES,
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    auth_service: AuthServiceDependency,
) -> UserResponse:
    client = client_key(request)
    account = payload.username
    retry_after_values = (
        request.app.state.login_client_limiter.check(client),
        request.app.state.login_account_limiter.check(account),
    )
    retry_after = max((value for value in retry_after_values if value is not None), default=None)
    if retry_after is not None:
        raise rate_limit_error(retry_after)
    user = auth_service.authenticate(
        session,
        payload.username,
        payload.password.get_secret_value(),
    )
    if user is None:
        retry_after_values = (
            request.app.state.login_client_limiter.consume(client),
            request.app.state.login_account_limiter.consume(account),
        )
        retry_after = max(
            (value for value in retry_after_values if value is not None),
            default=None,
        )
        if retry_after is not None:
            raise rate_limit_error(retry_after)
        raise ApiError(401, "invalid_credentials", "Invalid username or password")

    # A successful login does not erase either failure dimension. Entries
    # expire with their bounded sliding windows instead.
    cookies = request.app.state.cookies
    response.set_cookie(
        key=cookies.cookie_name,
        value=cookies.create(user.id),
        max_age=cookies.max_age,
        expires=datetime.now(UTC) + timedelta(seconds=cookies.max_age),
        path=cookies.cookie_path,
        domain=cookies.cookie_domain,
        secure=cookies.secure,
        httponly=True,
        samesite=cookies.samesite,
    )
    return UserResponse(data=UserPublic.model_validate(user))


@router.post(
    "/logout",
    response_model=MessageResponse,
)
def logout(
    request: Request,
    response: Response,
) -> MessageResponse:
    cookies = request.app.state.cookies
    response.delete_cookie(
        key=cookies.cookie_name,
        path=cookies.cookie_path,
        domain=cookies.cookie_domain,
        secure=cookies.secure,
        httponly=True,
        samesite=cookies.samesite,
    )
    return MessageResponse(data={"message": "Logged out"})


@router.get(
    "/me",
    response_model=UserResponse,
    responses={401: AUTH_ERROR_RESPONSES[401]},
)
def me(user: CurrentUser) -> UserResponse:
    return UserResponse(data=UserPublic.model_validate(user))
