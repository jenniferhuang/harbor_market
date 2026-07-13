from __future__ import annotations

import logging

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.dependencies import DbSession
from app.core.errors import ApiError
from app.schemas.auth import ErrorResponse, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={
        503: {"model": ErrorResponse, "description": "Database is unavailable"},
    },
)
def health(session: DbSession) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Database readiness check failed: %s", type(exc).__name__)
        raise ApiError(503, "database_unavailable", "Database is unavailable") from exc
    return HealthResponse(data={"status": "ok", "database": "ok"})
