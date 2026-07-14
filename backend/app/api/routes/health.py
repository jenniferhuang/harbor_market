from __future__ import annotations

import logging

from fastapi import APIRouter, Request
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
        503: {"model": ErrorResponse, "description": "A required dependency is unavailable"},
    },
)
def health(request: Request, session: DbSession) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        logger.warning("Database readiness check failed: %s", type(exc).__name__)
        raise ApiError(503, "database_unavailable", "Database is unavailable") from exc
    if request.app.state.settings.storage_backend == "disabled":
        storage_status = "disabled"
    else:
        try:
            request.app.state.object_storage.ensure_bucket()
        except Exception as exc:
            logger.warning("Object-storage readiness check failed: %s", type(exc).__name__)
            raise ApiError(503, "storage_unavailable", "Object storage is unavailable") from exc
        storage_status = "ok"
    return HealthResponse(data={"status": "ok", "database": "ok", "storage": storage_status})
