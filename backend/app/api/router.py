from fastapi import APIRouter

from app.api.routes import (
    admin_catalog,
    auth,
    catalog,
    health,
    mini_auth,
    payments,
    shop_commerce,
    shop_home,
    shop_store,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(mini_auth.router)
api_router.include_router(health.router)
api_router.include_router(admin_catalog.router)
api_router.include_router(catalog.router)
api_router.include_router(catalog.media_router)
api_router.include_router(payments.provider_router)
api_router.include_router(payments.admin_router)
api_router.include_router(shop_home.router)
api_router.include_router(shop_home.admin_router)
api_router.include_router(shop_commerce.mini_router)
api_router.include_router(shop_commerce.admin_router)
api_router.include_router(shop_store.mini_router)
api_router.include_router(shop_store.admin_router)
api_router.include_router(shop_store.public_router)
