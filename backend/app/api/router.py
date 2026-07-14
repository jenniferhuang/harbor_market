from fastapi import APIRouter

from app.api.routes import admin_catalog, auth, catalog, health

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(health.router)
api_router.include_router(admin_catalog.router)
api_router.include_router(catalog.router)
api_router.include_router(catalog.media_router)
