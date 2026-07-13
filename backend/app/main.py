from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.rate_limit import SlidingWindowRateLimiter
from app.core.security import PasswordManager, SessionCookieManager
from app.db.session import build_engine, build_session_factory
from app.middleware import SecurityHeadersMiddleware
from app.services.auth import AuthService


def create_app(settings: Settings | None = None, *, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    database_engine = engine or build_engine(settings)

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Harbor Market registration and signed-cookie authentication API.",
    )
    app.state.settings = settings
    app.state.engine = database_engine
    app.state.session_factory = build_session_factory(database_engine)
    app.state.cookies = SessionCookieManager(settings)
    app.state.auth_service = AuthService(PasswordManager(settings))
    app.state.registration_limiter = SlidingWindowRateLimiter(
        settings.registration_rate_limit,
        settings.registration_rate_window_seconds,
        max_keys=settings.rate_limit_max_keys,
    )
    app.state.login_limiter = SlidingWindowRateLimiter(
        settings.login_failure_rate_limit,
        settings.login_failure_rate_window_seconds,
        max_keys=settings.rate_limit_max_keys,
    )

    if settings.parsed_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.parsed_cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
        )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.parsed_allowed_hosts)
    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=settings.environment == "production",
    )
    install_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
