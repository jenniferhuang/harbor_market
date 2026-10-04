from __future__ import annotations

import json
import traceback
from dataclasses import asdict
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from pydantic import SecretStr

from app.core.errors import ApiError
from app.wechat import WechatIdentity, build_wechat_auth_provider
from app.wechat.auth import (
    DisabledWechatAuthProvider,
    LiveWechatAuthProvider,
    WechatHttpResponse,
)

APP_ID = "wx1234567890abcdef"
APP_SECRET = "server-secret-not-for-clients"
LOGIN_CODE = "temporary-login-code_123"
OPENID = "openid-from-official-exchange"
SESSION_KEY = "private-session-key"


class FakeTransport:
    def __init__(
        self,
        *,
        payload: Any = None,
        body: bytes | None = None,
        status: int = 200,
        error: Exception | None = None,
    ) -> None:
        self.body = body if body is not None else json.dumps(payload).encode()
        self.status = status
        self.error = error
        self.calls: list[tuple[str, float, int]] = []

    def __call__(
        self,
        path: str,
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> WechatHttpResponse:
        self.calls.append((path, timeout_seconds, max_bytes))
        if self.error is not None:
            raise self.error
        return WechatHttpResponse(self.status, self.body)


def _provider(transport: FakeTransport) -> LiveWechatAuthProvider:
    return LiveWechatAuthProvider(
        app_id=APP_ID,
        app_secret=APP_SECRET,
        timeout_seconds=5,
        transport=transport,
    )


def _assert_safe_error(exc: ApiError, status: int) -> None:
    assert exc.status_code == status
    assert any("\u4e00" <= character <= "\u9fff" for character in exc.message)
    rendered = "".join(traceback.format_exception(exc))
    for sensitive in (APP_SECRET, LOGIN_CODE, SESSION_KEY, "upstream-diagnostic"):
        assert sensitive not in rendered


def test_success_uses_server_credentials_and_returns_only_scoped_identity() -> None:
    transport = FakeTransport(
        payload={
            "openid": OPENID,
            "session_key": SESSION_KEY,
            "unionid": "not-used-for-account-linking",
        }
    )
    provider = _provider(transport)

    identity = provider.exchange_code(LOGIN_CODE)

    assert identity == WechatIdentity(app_id=APP_ID, openid=OPENID)
    assert asdict(identity) == {"app_id": APP_ID, "openid": OPENID}
    assert "session_key" not in provider.__dict__
    assert len(transport.calls) == 1
    path, timeout, max_bytes = transport.calls[0]
    parsed = urlsplit(path)
    assert parsed.scheme == ""
    assert parsed.netloc == ""
    assert parsed.path == "/sns/jscode2session"
    assert parse_qs(parsed.query) == {
        "appid": [APP_ID],
        "secret": [APP_SECRET],
        "js_code": [LOGIN_CODE],
        "grant_type": ["authorization_code"],
    }
    assert timeout == 5
    assert max_bytes == 16 * 1024


@pytest.mark.parametrize("code", ["", " ", "invalid code", "\n", "x" * 257, None, 123])
def test_invalid_login_codes_never_contact_tencent(code: Any) -> None:
    transport = FakeTransport(payload={"openid": OPENID, "session_key": SESSION_KEY})
    with pytest.raises(ApiError) as caught:
        _provider(transport).exchange_code(code)
    _assert_safe_error(caught.value, 400)
    assert transport.calls == []


@pytest.mark.parametrize("errcode", [40029, 40163, 41008, 42003, 40226])
def test_invalid_expired_used_and_rejected_codes_are_safe_client_errors(errcode: int) -> None:
    transport = FakeTransport(payload={"errcode": errcode, "errmsg": "upstream-diagnostic"})
    with pytest.raises(ApiError) as caught:
        _provider(transport).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 400)
    assert caught.value.code == "wechat_login_code_invalid"


@pytest.mark.parametrize("errcode", [45009, 45011])
def test_tencent_quota_errors_are_rate_limited(errcode: int) -> None:
    transport = FakeTransport(payload={"errcode": errcode, "errmsg": "upstream-diagnostic"})
    with pytest.raises(ApiError) as caught:
        _provider(transport).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 429)
    assert caught.value.headers == {"Retry-After": "60"}


@pytest.mark.parametrize("errcode", [-1, 40001, 40002, 40013, 40125, 40164, 41002, 41004])
def test_tencent_configuration_and_transient_errors_are_unavailable(errcode: int) -> None:
    transport = FakeTransport(payload={"errcode": errcode, "errmsg": "upstream-diagnostic"})
    with pytest.raises(ApiError) as caught:
        _provider(transport).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 503)


@pytest.mark.parametrize(
    "status,expected",
    [(302, 502), (400, 502), (429, 429), (500, 502), (502, 502), (503, 503), (504, 503)],
)
def test_non_success_http_statuses_never_authenticate(status: int, expected: int) -> None:
    transport = FakeTransport(
        status=status,
        payload={"openid": OPENID, "session_key": SESSION_KEY},
    )
    with pytest.raises(ApiError) as caught:
        _provider(transport).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, expected)
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {},
        {"errcode": True, "openid": OPENID, "session_key": SESSION_KEY},
        {"errcode": "0", "openid": OPENID, "session_key": SESSION_KEY},
        {"errcode": 99999, "errmsg": "upstream-diagnostic"},
        {"openid": "", "session_key": SESSION_KEY},
        {"openid": "openid with spaces", "session_key": SESSION_KEY},
        {"openid": "x" * 129, "session_key": SESSION_KEY},
        {"openid": [OPENID], "session_key": SESSION_KEY},
        {"openid": OPENID},
        {"openid": OPENID, "session_key": ""},
        {"openid": OPENID, "session_key": "x" * 257},
        {"openid": OPENID, "session_key": 123},
    ],
)
def test_invalid_upstream_payloads_never_authenticate(payload: Any) -> None:
    with pytest.raises(ApiError) as caught:
        _provider(FakeTransport(payload=payload)).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 502)


@pytest.mark.parametrize("body", [b"not-json", b"\xff", b"x" * (16 * 1024 + 1)])
def test_malformed_and_oversized_bodies_are_safe_upstream_errors(body: bytes) -> None:
    with pytest.raises(ApiError) as caught:
        _provider(FakeTransport(body=body)).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 502)


@pytest.mark.parametrize("error_type", [TimeoutError, OSError, RuntimeError])
def test_transport_errors_do_not_expose_credentials_or_exception_context(error_type: Any) -> None:
    error = error_type(f"{APP_SECRET}/{LOGIN_CODE}/{SESSION_KEY}/upstream-diagnostic")
    with pytest.raises(ApiError) as caught:
        _provider(FakeTransport(error=error)).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 503)
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize(
    "mode,app_id,secret",
    [
        ("disabled", APP_ID, SecretStr(APP_SECRET)),
        ("mock", APP_ID, SecretStr(APP_SECRET)),
        ("live", None, SecretStr(APP_SECRET)),
        ("live", "not-a-wechat-app-id", SecretStr(APP_SECRET)),
        ("live", APP_ID, None),
        ("live", APP_ID, SecretStr("   ")),
    ],
)
def test_factory_never_fakes_success_for_disabled_or_missing_configuration(
    mode: str,
    app_id: str | None,
    secret: SecretStr | None,
) -> None:
    settings = SimpleNamespace(
        wechat_miniprogram_auth_mode=mode,
        wechat_miniprogram_app_id=app_id,
        wechat_miniprogram_app_secret=secret,
        wechat_miniprogram_timeout_seconds=5,
    )
    provider = build_wechat_auth_provider(settings)  # type: ignore[arg-type]
    assert isinstance(provider, DisabledWechatAuthProvider)
    with pytest.raises(ApiError) as caught:
        provider.exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 503)


def test_factory_builds_live_provider_only_with_explicit_live_configuration() -> None:
    settings = SimpleNamespace(
        wechat_miniprogram_auth_mode="live",
        wechat_miniprogram_app_id=APP_ID,
        wechat_miniprogram_app_secret=SecretStr(APP_SECRET),
        wechat_miniprogram_timeout_seconds=5,
    )
    provider = build_wechat_auth_provider(settings)  # type: ignore[arg-type]
    assert isinstance(provider, LiveWechatAuthProvider)
    assert provider.app_id == APP_ID


@pytest.mark.parametrize("timeout", [0, -1, 61, float("nan"), float("inf"), True, "5"])
def test_live_provider_requires_a_finite_bounded_timeout(timeout: Any) -> None:
    with pytest.raises(ApiError) as caught:
        LiveWechatAuthProvider(app_id=APP_ID, app_secret=APP_SECRET, timeout_seconds=timeout)
    _assert_safe_error(caught.value, 503)


class FakeHttpResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.read_amounts: list[int] = []
        self.closed = False

    def read(self, amount: int) -> bytes:
        self.read_amounts.append(amount)
        return self.body[:amount]

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [200, 302])
def test_default_transport_uses_fixed_https_host_bounded_read_and_no_redirects(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    response = FakeHttpResponse(
        json.dumps({"openid": OPENID, "session_key": SESSION_KEY}).encode(), status
    )
    connections: list[Any] = []

    class FakeHttpsConnection:
        def __init__(self, host: str, *, timeout: float) -> None:
            self.host = host
            self.timeout = timeout
            self.closed = False
            self.requests: list[tuple[str, str, dict[str, str]]] = []
            connections.append(self)

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            self.requests.append((method, path, headers))

        def getresponse(self) -> FakeHttpResponse:
            return response

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("app.wechat.auth.http.client.HTTPSConnection", FakeHttpsConnection)
    provider = LiveWechatAuthProvider(app_id=APP_ID, app_secret=APP_SECRET)
    if status == 200:
        assert provider.exchange_code(LOGIN_CODE) == WechatIdentity(APP_ID, OPENID)
    else:
        with pytest.raises(ApiError) as caught:
            provider.exchange_code(LOGIN_CODE)
        _assert_safe_error(caught.value, 502)
    assert len(connections) == 1
    assert connections[0].host == "api.weixin.qq.com"
    assert connections[0].timeout == 5
    assert len(connections[0].requests) == 1
    assert connections[0].requests[0][0] == "GET"
    assert response.read_amounts == [16 * 1024 + 1]
    assert response.closed is True
    assert connections[0].closed is True


def test_default_transport_closes_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeHttpResponse(b"x" * (16 * 1024 + 1))
    connection = SimpleNamespace(
        request=lambda *args, **kwargs: None,
        getresponse=lambda: response,
        closed=False,
    )
    connection.close = lambda: setattr(connection, "closed", True)
    monkeypatch.setattr(
        "app.wechat.auth.http.client.HTTPSConnection", lambda *args, **kwargs: connection
    )
    with pytest.raises(ApiError) as caught:
        LiveWechatAuthProvider(app_id=APP_ID, app_secret=APP_SECRET).exchange_code(LOGIN_CODE)
    _assert_safe_error(caught.value, 503)
    assert response.read_amounts == [16 * 1024 + 1]
    assert response.closed is True
    assert connection.closed is True
