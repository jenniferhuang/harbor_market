from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response

_CHINESE_PREFIXES = (
    "/api/v1/mini/auth/",
    "/api/v1/mini/shop/",
    "/api/v1/admin/shop/",
    "/api/v1/shop/",
)


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> Response:
        if not request.url.path.startswith(_CHINESE_PREFIXES):
            return await http_exception_handler(request, exc)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": "mini_request_invalid",
                    "message": "请求无法处理，请检查参数后重试",
                }
            },
            headers=exc.headers,
        )

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        message = exc.message
        if request.url.path.startswith(_CHINESE_PREFIXES) and not any(
            "\u4e00" <= character <= "\u9fff" for character in message
        ):
            message = {
                "authentication_required": "请先登录",
                "admin_required": "需要管理员权限",
                "csrf_origin_mismatch": "请求来源不受信任",
                "product_not_found": "商品不存在或暂未上架",
                "category_not_found": "分类不存在",
            }.get(exc.code, "请求暂时无法处理，请稍后重试")
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": message}},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        is_mini_auth = request.url.path.startswith(_CHINESE_PREFIXES)
        fields: list[dict[str, Any]] = []
        for error in exc.errors():
            location = [str(part) for part in error.get("loc", ()) if part not in {"body", "query"}]
            fields.append(
                {
                    "field": ".".join(location) or "request",
                    "message": "参数无效，请检查后重试"
                    if is_mini_auth
                    else error.get("msg", "Invalid value"),
                }
            )
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "请求参数无效，请检查后重试"
                    if is_mini_auth
                    else "Request validation failed",
                    "fields": fields,
                },
                "detail": fields,
            },
        )
