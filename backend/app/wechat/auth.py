from __future__ import annotations

import http.client
import json
import math
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlencode

from app.core.config import Settings
from app.core.errors import ApiError

_WECHAT_HOST = "api.weixin.qq.com"
_CODE2SESSION_PATH = "/sns/jscode2session"
_MAX_RESPONSE_BYTES = 16 * 1024
_APP_ID_PATTERN = re.compile(r"wx[A-Za-z0-9]{16}\Z")
_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_OPENID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


@dataclass(frozen=True, slots=True)
class WechatIdentity:
    app_id: str
    openid: str


class WechatAuthProvider(Protocol):
    def exchange_code(self, code: str) -> WechatIdentity: ...


@dataclass(frozen=True, slots=True)
class WechatHttpResponse:
    status: int
    body: bytes


class WechatAuthTransport(Protocol):
    def __call__(
        self,
        path: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> WechatHttpResponse: ...


def _unavailable() -> ApiError:
    return ApiError(503, "wechat_login_unavailable", "微信登录暂不可用，请稍后重试")


def _upstream_error() -> ApiError:
    return ApiError(502, "wechat_login_upstream_error", "微信登录服务异常，请稍后重试")


def _invalid_code() -> ApiError:
    return ApiError(400, "wechat_login_code_invalid", "微信登录凭证无效或已过期，请重新登录")


def _rate_limited() -> ApiError:
    return ApiError(
        429,
        "wechat_login_rate_limited",
        "微信登录请求过于频繁，请稍后重试",
        headers={"Retry-After": "60"},
    )


class DisabledWechatAuthProvider:
    def exchange_code(self, code: str) -> WechatIdentity:
        del code
        raise _unavailable()


def _request_code2session(
    path: str,
    *,
    timeout_seconds: float,
    max_bytes: int,
) -> WechatHttpResponse:
    # http.client connects directly to this fixed HTTPS host and never follows
    # redirects. In particular, credentials cannot be forwarded to a Location URL.
    connection = http.client.HTTPSConnection(_WECHAT_HOST, timeout=timeout_seconds)
    response: http.client.HTTPResponse | None = None
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError("WeChat response exceeded its size limit")
        return WechatHttpResponse(status=response.status, body=body)
    finally:
        if response is not None:
            response.close()
        connection.close()


class LiveWechatAuthProvider:
    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        timeout_seconds: float = 5,
        transport: WechatAuthTransport | None = None,
    ) -> None:
        if (
            not isinstance(app_id, str)
            or _APP_ID_PATTERN.fullmatch(app_id) is None
            or not isinstance(app_secret, str)
            or not app_secret.strip()
            or not isinstance(timeout_seconds, int | float)
            or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60
        ):
            raise _unavailable()
        self.app_id = app_id
        self._app_secret = app_secret
        self._timeout_seconds = float(timeout_seconds)
        self._transport = transport if transport is not None else _request_code2session

    def exchange_code(self, code: str) -> WechatIdentity:
        if not isinstance(code, str) or _CODE_PATTERN.fullmatch(code) is None:
            raise _invalid_code()
        query = urlencode(
            {
                "appid": self.app_id,
                "secret": self._app_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            }
        )
        try:
            response = self._transport(
                f"{_CODE2SESSION_PATH}?{query}",
                timeout_seconds=self._timeout_seconds,
                max_bytes=_MAX_RESPONSE_BYTES,
            )
        except Exception:
            # Exceptions may include the request URL, login code, or AppSecret.
            # Neither their message nor their traceback context is propagated.
            raise _unavailable() from None

        if not isinstance(response, WechatHttpResponse):
            raise _upstream_error()
        if type(response.status) is not int or not isinstance(response.body, bytes):
            raise _upstream_error()
        if len(response.body) > _MAX_RESPONSE_BYTES:
            raise _upstream_error()
        if response.status == 429:
            raise _rate_limited()
        if response.status in {503, 504}:
            raise _unavailable()
        if response.status != 200:
            raise _upstream_error()
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError):
            raise _upstream_error() from None
        if not isinstance(payload, dict):
            raise _upstream_error()

        errcode = payload.get("errcode", 0)
        if type(errcode) is not int:
            raise _upstream_error()
        if errcode in {40029, 40163, 41008, 42003, 40226}:
            raise _invalid_code()
        if errcode in {45009, 45011}:
            raise _rate_limited()
        if errcode in {-1, 40001, 40002, 40013, 40125, 40164, 41002, 41004}:
            raise _unavailable()
        if errcode != 0:
            raise _upstream_error()

        openid = payload.get("openid")
        session_key = payload.get("session_key")
        if (
            not isinstance(openid, str)
            or _OPENID_PATTERN.fullmatch(openid) is None
            or not isinstance(session_key, str)
            or not 1 <= len(session_key) <= 256
        ):
            raise _upstream_error()
        # session_key and unionid are deliberately discarded. Only the official
        # exchange's AppID-scoped OpenID establishes this application's identity.
        return WechatIdentity(app_id=self.app_id, openid=openid)


def build_wechat_auth_provider(settings: Settings) -> WechatAuthProvider:
    if settings.wechat_miniprogram_auth_mode != "live":
        return DisabledWechatAuthProvider()
    app_id = settings.wechat_miniprogram_app_id
    secret = settings.wechat_miniprogram_app_secret
    if (
        not isinstance(app_id, str)
        or _APP_ID_PATTERN.fullmatch(app_id) is None
        or secret is None
        or not secret.get_secret_value().strip()
    ):
        return DisabledWechatAuthProvider()
    return LiveWechatAuthProvider(
        app_id=app_id,
        app_secret=secret.get_secret_value(),
        timeout_seconds=settings.wechat_miniprogram_timeout_seconds,
    )
