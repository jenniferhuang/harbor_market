from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models import User
from app.services.auth import AuthService


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_auth_service(request: Request) -> AuthService:
    return request.app.state.auth_service


DbSession = Annotated[Session, Depends(get_db)]
AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]


def get_current_user(
    request: Request,
    session: DbSession,
    auth_service: AuthServiceDependency,
) -> User:
    cookies = request.app.state.cookies
    token = request.cookies.get(cookies.cookie_name)
    user_id = cookies.verify(token) if token else None
    user = auth_service.get_active_user(session, user_id) if user_id is not None else None
    if user is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def enforce_registration_rate_limit(request: Request) -> None:
    retry_after = request.app.state.registration_limiter.consume(client_key(request))
    if retry_after is not None:
        raise _rate_limit_error(retry_after)


def enforce_login_rate_limit(request: Request) -> None:
    retry_after = request.app.state.login_limiter.check(client_key(request))
    if retry_after is not None:
        raise _rate_limit_error(retry_after)


def _rate_limit_error(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate_limit_exceeded",
        "Too many attempts. Try again later",
        headers={"Retry-After": str(retry_after)},
    )
