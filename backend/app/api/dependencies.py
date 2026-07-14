from __future__ import annotations

from collections.abc import Iterator
from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlsplit

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
    user = get_optional_current_user(request, session, auth_service)
    if user is None:
        raise ApiError(
            401,
            "authentication_required",
            "Authentication is required",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return user


def get_optional_current_user(
    request: Request,
    session: DbSession,
    auth_service: AuthServiceDependency,
) -> User | None:
    cookies = request.app.state.cookies
    token = request.cookies.get(cookies.cookie_name)
    user_id = cookies.verify(token) if token else None
    return auth_service.get_active_user(session, user_id) if user_id is not None else None


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalCurrentUser = Annotated[User | None, Depends(get_optional_current_user)]


def get_current_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise ApiError(403, "admin_required", "Administrator permission is required")
    return user


def require_same_origin_for_unsafe_request(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    source = request.headers.get("origin") or request.headers.get("referer")
    if source is None:
        # CLI clients may omit both. Browsers send Origin on cross-origin unsafe
        # requests, including multipart forms, which is the CSRF case we reject.
        return
    if source == "null":
        raise ApiError(403, "csrf_origin_mismatch", "Request origin is not trusted")
    parsed = urlsplit(source)
    forwarded_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    expected_scheme = forwarded_scheme.split(",", 1)[0].strip().casefold()
    forwarded_host = request.headers.get("x-forwarded-host")
    expected_host = (forwarded_host or request.headers.get("host", "")).split(",", 1)[0].strip()
    expected = urlsplit(f"{expected_scheme}://{expected_host}")
    try:
        source_port = parsed.port or (443 if parsed.scheme.casefold() == "https" else 80)
        expected_port = expected.port or (443 if expected_scheme == "https" else 80)
    except ValueError as exc:
        raise ApiError(403, "csrf_origin_mismatch", "Request origin is not trusted") from exc
    if (
        parsed.scheme.casefold() != expected_scheme
        or (parsed.hostname or "").casefold() != (expected.hostname or "").casefold()
        or source_port != expected_port
    ):
        raise ApiError(403, "csrf_origin_mismatch", "Request origin is not trusted")


AdminUser = Annotated[User, Depends(get_current_admin)]


def client_key(request: Request) -> str:
    if request.app.state.settings.trust_proxy_headers:
        forwarded_client = request.headers.get("x-real-ip", "").strip()
        try:
            return ip_address(forwarded_client).compressed
        except ValueError:
            pass
    return request.client.host if request.client is not None else "unknown"


def enforce_registration_rate_limit(request: Request) -> None:
    retry_after = request.app.state.registration_limiter.consume(client_key(request))
    if retry_after is not None:
        raise rate_limit_error(retry_after)


def rate_limit_error(retry_after: int) -> ApiError:
    return ApiError(
        429,
        "rate_limit_exceeded",
        "Too many attempts. Try again later",
        headers={"Retry-After": str(retry_after)},
    )
