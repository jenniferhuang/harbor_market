from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import pytest
from conftest import FakeObjectStorage
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from PIL import Image, PngImagePlugin
from sqlalchemy import func, select

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.rate_limit import SlidingWindowRateLimiter
from app.main import create_app
from app.models import User
from app.models.mini_customer import MiniCustomer, MiniSession
from app.wechat.auth import WechatIdentity

AUTH = "/api/v1/mini/auth"
APP_ID = "wx1234567890abcdef"


class FakeWechatAuthProvider:
    """An explicit test dependency; production never receives this provider."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.identities: dict[str, WechatIdentity] = {}
        self.rejected_codes: set[str] = set()

    def exchange_code(self, code: str) -> WechatIdentity:
        self.calls.append(code)
        if code in self.rejected_codes:
            raise ApiError(400, "wechat_login_code_invalid", "登录凭证无效，请重新登录")
        return self.identities.get(code, WechatIdentity(app_id=APP_ID, openid="test-openid"))


@pytest.fixture
def mini_provider(app: FastAPI) -> FakeWechatAuthProvider:
    provider = FakeWechatAuthProvider()
    app.state.wechat_auth_provider = provider
    return provider


def _login(client: TestClient, code: str = "test-login-code") -> dict[str, Any]:
    response = client.post(f"{AUTH}/login", json={"code": code})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _bearer(data: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {data['access_token']}"}


def _assert_private(response: Response) -> None:
    assert response.headers["cache-control"] == "private, no-store"


def _image_bytes(
    image_format: str = "PNG",
    *,
    size: tuple[int, int] = (1024, 768),
    metadata: bool = False,
) -> bytes:
    image = Image.new("RGB", size, color=(36, 115, 168))
    output = BytesIO()
    options: dict[str, Any] = {}
    if metadata:
        info = PngImagePlugin.PngInfo()
        info.add_text("Comment", "private-source-metadata")
        options["pnginfo"] = info
    image.save(output, format=image_format, **options)
    return output.getvalue()


def _animated_png() -> bytes:
    output = BytesIO()
    first = Image.new("RGB", (8, 8), "red")
    second = Image.new("RGB", (8, 8), "blue")
    first.save(output, format="PNG", save_all=True, append_images=[second], duration=100, loop=0)
    return output.getvalue()


def _upload(client: TestClient, login: dict[str, Any], payload: bytes) -> Response:
    return client.post(
        f"{AUTH}/avatar",
        headers=_bearer(login),
        files={"file": ("avatar.png", payload, "image/png")},
    )


def test_default_login_is_disabled_without_creating_customer_or_browser_session(
    client: TestClient,
    app: FastAPI,
) -> None:
    assert app.state.settings.wechat_miniprogram_auth_mode == "disabled"

    response = client.post(f"{AUTH}/login", json={"code": "no-provider-code"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "wechat_login_unavailable"
    assert any(
        "\u4e00" <= character <= "\u9fff" for character in response.json()["error"]["message"]
    )
    assert "set-cookie" not in response.headers
    _assert_private(response)
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MiniCustomer)) == 0
        assert session.scalar(select(func.count()).select_from(MiniSession)) == 0
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_live_login_with_missing_credentials_is_unavailable(
    app: FastAPI,
    settings: Settings,
    fake_object_storage: FakeObjectStorage,
) -> None:
    live_settings = settings.model_copy(
        update={
            "wechat_miniprogram_auth_mode": "live",
            "wechat_miniprogram_app_id": None,
            "wechat_miniprogram_app_secret": None,
        }
    )
    live_app = create_app(
        live_settings, engine=app.state.engine, object_storage=fake_object_storage
    )
    with TestClient(live_app) as live_client:
        response = live_client.post(f"{AUTH}/login", json={"code": "missing-config-code"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "wechat_login_unavailable"
    assert any(
        "\u4e00" <= character <= "\u9fff" for character in response.json()["error"]["message"]
    )


def test_create_app_accepts_explicit_provider_injection(
    app: FastAPI,
    settings: Settings,
    fake_object_storage: FakeObjectStorage,
) -> None:
    provider = FakeWechatAuthProvider()
    injected_app = create_app(
        settings,
        engine=app.state.engine,
        object_storage=fake_object_storage,
        wechat_auth_provider=provider,
    )
    assert injected_app.state.wechat_auth_provider is provider
    with TestClient(injected_app) as injected_client:
        data = _login(injected_client, "injected-provider-code")
        assert injected_client.get(f"{AUTH}/me", headers=_bearer(data)).status_code == 200
    assert provider.calls == ["injected-provider-code"]


def test_login_contract_is_private_and_only_hashes_token_and_code_in_database(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    code = "private-single-use-login-code"
    response = client.post(f"{AUTH}/login", json={"code": code})
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert set(response.json()) == {"data"}
    assert set(data) == {"access_token", "token_type", "expires_at", "customer"}
    assert data["token_type"] == "Bearer"
    assert isinstance(data["access_token"], str) and len(data["access_token"]) >= 43
    expiry = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    assert expiry.tzinfo is not None and expiry > datetime.now(UTC)
    assert set(data["customer"]) == {"id", "nickname", "avatar_url"}
    assert isinstance(data["customer"]["id"], int)
    assert data["customer"]["avatar_url"] is None
    assert code not in response.text
    assert "test-openid" not in response.text
    assert APP_ID not in response.text
    assert "set-cookie" not in response.headers
    _assert_private(response)
    assert mini_provider.calls == [code]

    me = client.get(f"{AUTH}/me", headers=_bearer(data))
    assert me.status_code == 200
    assert me.json() == {"data": data["customer"]}
    _assert_private(me)
    with app.state.session_factory() as session:
        customer = session.get(MiniCustomer, data["customer"]["id"])
        assert customer is not None
        assert (customer.app_id, customer.openid) == (APP_ID, "test-openid")
        record = session.scalar(select(MiniSession))
        assert record is not None
        assert record.customer_id == customer.id
        assert record.token_hash == hashlib.sha256(data["access_token"].encode()).hexdigest()
        assert record.login_code_hash == hashlib.sha256(code.encode()).hexdigest()
        assert data["access_token"] not in record.token_hash
        assert code not in record.login_code_hash
        assert session.scalar(select(func.count()).select_from(User)) == 0


def test_server_identity_is_reused_and_login_codes_cannot_be_replayed(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    first = _login(client, "first-code")
    second = _login(client, "second-code")

    assert first["customer"]["id"] == second["customer"]["id"]
    assert first["access_token"] != second["access_token"]
    replay = client.post(f"{AUTH}/login", json={"code": "first-code"})
    assert replay.status_code == 400
    assert replay.json()["error"]["code"] == "mini_login_code_reused"
    assert client.get(f"{AUTH}/me", headers=_bearer(first)).status_code == 200
    assert client.get(f"{AUTH}/me", headers=_bearer(second)).status_code == 200


@pytest.mark.parametrize("field", ["openid", "app_id", "is_admin", "role", "avatar_url"])
def test_login_rejects_client_supplied_identity_and_privileges_before_provider_exchange(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    field: str,
) -> None:
    response = client.post(
        f"{AUTH}/login", json={"code": "not-exchanged-code", field: "attacker-controlled"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert mini_provider.calls == []
    assert "not-exchanged-code" not in response.text


@pytest.mark.parametrize(
    "code", [None, 123, "", "   ", "x" * 257], ids=["null", "number", "empty", "spaces", "too-long"]
)
def test_invalid_login_code_is_rejected_before_provider_exchange(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    code: object,
) -> None:
    response = client.post(f"{AUTH}/login", json={"code": code})

    assert response.status_code == 422
    assert mini_provider.calls == []


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/me", None),
        ("PATCH", "/profile", {"nickname": "未授权用户"}),
        ("POST", "/avatar", None),
        ("GET", "/avatar", None),
        ("POST", "/logout", None),
    ],
)
def test_customer_endpoints_require_bearer_authentication(
    client: TestClient,
    method: str,
    path: str,
    payload: dict[str, str] | None,
) -> None:
    options: dict[str, Any] = {"json": payload} if payload is not None else {}
    if method == "POST" and path == "/avatar":
        options["files"] = {"file": ("avatar.png", _image_bytes(size=(8, 8)), "image/png")}
    response = client.request(method, f"{AUTH}{path}", **options)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "mini_authentication_required"
    assert response.headers["www-authenticate"] == "Bearer"
    _assert_private(response)


@pytest.mark.parametrize("authorization", ["Basic token", "Bearer", "Bearer garbage", "Bearer a b"])
def test_malformed_bearer_header_is_rejected(client: TestClient, authorization: str) -> None:
    response = client.get(f"{AUTH}/me", headers={"Authorization": authorization})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "mini_authentication_required"


def test_mini_bearer_does_not_authenticate_browser_or_admin_routes(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    data = _login(client)
    client.cookies.clear()

    assert client.get("/api/v1/auth/me", headers=_bearer(data)).status_code == 401
    assert client.get("/api/v1/admin/products", headers=_bearer(data)).status_code == 401
    assert (
        client.get(f"{AUTH}/me", params={"access_token": data["access_token"]}).status_code == 401
    )
    assert client.post("/api/v1/auth/logout").status_code == 200
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).status_code == 200


def test_provider_identity_must_match_configured_app_id_before_creating_a_customer(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    app.state.settings.wechat_miniprogram_app_id = "wxotherapp12345678"

    response = client.post(f"{AUTH}/login", json={"code": "wrong-app-code"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "mini_identity_invalid"
    with app.state.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(MiniCustomer)) == 0
        assert session.scalar(select(func.count()).select_from(MiniSession)) == 0


def test_bearer_identity_is_bound_to_the_configured_app_id(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    app.state.settings.wechat_miniprogram_app_id = APP_ID
    data = _login(client)
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).status_code == 200
    app.state.settings.wechat_miniprogram_app_id = "wxotherapp12345678"

    assert client.get(f"{AUTH}/me", headers=_bearer(data)).status_code == 401


def test_admin_cookie_does_not_authenticate_mini_and_mini_logout_keeps_browser_session(
    admin_client: TestClient,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    assert admin_client.get("/api/v1/auth/me").json()["data"]["is_admin"] is True
    assert admin_client.get(f"{AUTH}/me").status_code == 401
    assert (
        admin_client.get(f"{AUTH}/me", headers={"Authorization": "Bearer invalid"}).status_code
        == 401
    )
    data = _login(admin_client)

    response = admin_client.post(f"{AUTH}/logout", headers=_bearer(data))

    assert response.status_code == 200
    assert "set-cookie" not in response.headers
    assert admin_client.get("/api/v1/auth/me").json()["data"]["is_admin"] is True
    assert admin_client.get(f"{AUTH}/me", headers=_bearer(data)).status_code == 401


def test_logout_revokes_only_the_presented_session(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    first = _login(client, "session-one")
    second = _login(client, "session-two")

    logout = client.post(f"{AUTH}/logout", headers=_bearer(first))

    assert logout.status_code == 200
    assert logout.json() == {"data": {"logged_out": True}}
    _assert_private(logout)
    assert client.get(f"{AUTH}/me", headers=_bearer(first)).status_code == 401
    assert (
        client.patch(
            f"{AUTH}/profile", headers=_bearer(first), json={"nickname": "拒绝"}
        ).status_code
        == 401
    )
    assert client.get(f"{AUTH}/me", headers=_bearer(second)).status_code == 200
    with app.state.session_factory() as session:
        records = list(session.scalars(select(MiniSession).order_by(MiniSession.id)))
        assert len(records) == 2
        assert records[0].revoked_at is not None
        assert records[1].revoked_at is None


def test_expired_bearer_is_denied_even_when_browser_admin_is_authenticated(
    admin_client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _login(admin_client)
    expiry = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    monkeypatch.setattr(
        app.state.mini_auth_service, "_clock", lambda: expiry + timedelta(seconds=1)
    )

    expired = admin_client.get(f"{AUTH}/me", headers=_bearer(data))

    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "mini_authentication_required"
    assert admin_client.get("/api/v1/auth/me").status_code == 200


def test_inactive_customer_cannot_use_an_existing_session(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    data = _login(client)
    with app.state.session_factory() as session:
        customer = session.get(MiniCustomer, data["customer"]["id"])
        assert customer is not None
        customer.is_active = False
        session.commit()

    assert client.get(f"{AUTH}/me", headers=_bearer(data)).status_code == 401


def test_profile_update_is_trimmed_and_persisted_for_the_authenticated_customer(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    first = _login(client, "customer-one")
    mini_provider.identities["customer-two"] = WechatIdentity(app_id=APP_ID, openid="other-openid")
    second = _login(client, "customer-two")

    profile = client.patch(
        f"{AUTH}/profile", headers=_bearer(first), json={"nickname": "  海港顾客  "}
    )

    assert profile.status_code == 200
    assert profile.json() == {"data": {**first["customer"], "nickname": "海港顾客"}}
    _assert_private(profile)
    assert client.get(f"{AUTH}/me", headers=_bearer(first)).json() == profile.json()
    assert client.get(f"{AUTH}/me", headers=_bearer(second)).json() == {"data": second["customer"]}
    relogin = _login(client, "customer-one-again")
    assert relogin["customer"] == profile.json()["data"]


def test_profile_accepts_64_unicode_codepoints_and_rejects_65_without_changing_name(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    data = _login(client)
    nickname = "🌊" * 64

    accepted = client.patch(f"{AUTH}/profile", headers=_bearer(data), json={"nickname": nickname})

    assert accepted.status_code == 200
    assert accepted.json() == {"data": {**data["customer"], "nickname": nickname}}
    rejected = client.patch(
        f"{AUTH}/profile", headers=_bearer(data), json={"nickname": nickname + "🌊"}
    )
    assert rejected.status_code == 422
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).json() == accepted.json()


@pytest.mark.parametrize("nickname", ["", "   ", "x" * 65, "bad\nname", "bad\x00name", 42, None])
def test_invalid_profile_nickname_does_not_modify_the_customer(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    nickname: object,
) -> None:
    data = _login(client)

    response = client.patch(f"{AUTH}/profile", headers=_bearer(data), json={"nickname": nickname})

    assert response.status_code == 422
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).json() == {"data": data["customer"]}


@pytest.mark.parametrize("field", ["id", "openid", "is_admin", "role", "avatar_url"])
def test_profile_rejects_extra_fields_including_remote_avatar_urls(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    field: str,
) -> None:
    data = _login(client)
    response = client.patch(
        f"{AUTH}/profile",
        headers=_bearer(data),
        json={"nickname": "安全昵称", field: "https://attacker.example/tracking.png"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).json() == {"data": data["customer"]}


def test_successful_and_failed_login_attempts_share_one_bounded_rate_limit(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    assert app.state.mini_login_rate_limiter.limit == 10
    assert app.state.mini_login_rate_limiter.window_seconds == 60
    app.state.mini_login_rate_limiter = SlidingWindowRateLimiter(2, 60, max_keys=100)
    mini_provider.rejected_codes.add("rejected-code")
    rejected = client.post(f"{AUTH}/login", json={"code": "rejected-code"})
    assert rejected.status_code == 400
    _login(client, "accepted-code")

    limited = client.post(f"{AUTH}/login", json={"code": "third-code"})

    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "mini_login_rate_limited"
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert mini_provider.calls == ["rejected-code", "accepted-code"]
    _assert_private(limited)


def test_validation_failures_also_consume_login_attempts(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    app.state.mini_login_rate_limiter = SlidingWindowRateLimiter(2, 60, max_keys=100)
    assert client.post(f"{AUTH}/login", json={"code": ""}).status_code == 422
    assert (
        client.post(f"{AUTH}/login", json={"code": "second", "openid": "forged"}).status_code == 422
    )

    response = client.post(f"{AUTH}/login", json={"code": "valid-third-code"})

    assert response.status_code == 429
    assert mini_provider.calls == []


def test_malformed_json_consumes_login_attempts_before_body_parsing(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    app.state.mini_login_rate_limiter = SlidingWindowRateLimiter(1, 60, max_keys=100)
    malformed_headers = {"Content-Type": "application/json"}

    first = client.post(f"{AUTH}/login", content=b'{"code":', headers=malformed_headers)
    second = client.post(f"{AUTH}/login", content=b'{"code":', headers=malformed_headers)
    valid = client.post(f"{AUTH}/login", json={"code": "valid-code-after-malformed"})

    assert first.status_code == 422
    assert first.json()["error"]["code"] == "validation_error"
    assert second.status_code == valid.status_code == 429
    assert second.json()["error"]["code"] == "mini_login_rate_limited"
    assert valid.json()["error"]["code"] == "mini_login_rate_limited"
    assert mini_provider.calls == []
    for response in (first, second, valid):
        _assert_private(response)


def test_malformed_multipart_returns_private_chinese_error_without_storage_writes(
    client: TestClient,
    fake_object_storage: FakeObjectStorage,
) -> None:
    response = client.post(
        f"{AUTH}/avatar",
        content=b"malformed multipart with no boundary",
        headers={"Content-Type": "multipart/form-data"},
    )

    assert response.status_code == 400
    assert set(response.json()) == {"error"}
    error = response.json()["error"]
    assert set(error) == {"code", "message"}
    assert error["code"] == "mini_request_invalid"
    assert any("\u4e00" <= character <= "\u9fff" for character in error["message"])
    assert fake_object_storage.put_calls == []
    _assert_private(response)


def test_untrusted_real_ip_header_cannot_bypass_mini_login_rate_limit(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
) -> None:
    app.state.mini_login_rate_limiter = SlidingWindowRateLimiter(1, 60, max_keys=100)
    first = client.post(
        f"{AUTH}/login", json={"code": "first-code"}, headers={"X-Real-IP": "198.51.100.1"}
    )
    assert first.status_code == 200

    second = client.post(
        f"{AUTH}/login", json={"code": "second-code"}, headers={"X-Real-IP": "198.51.100.2"}
    )

    assert second.status_code == 429
    assert mini_provider.calls == ["first-code"]


@pytest.mark.parametrize(
    ("image_format", "content_type"),
    [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")],
)
def test_avatar_accepts_supported_images_and_stores_private_bounded_jpeg(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
    fake_object_storage: FakeObjectStorage,
    image_format: str,
    content_type: str,
) -> None:
    data = _login(client)
    source = _image_bytes(image_format, metadata=image_format == "PNG")
    response = client.post(
        f"{AUTH}/avatar",
        headers=_bearer(data),
        files={"file": (f"avatar.{image_format.lower()}", source, content_type)},
    )

    assert response.status_code == 200, response.text
    customer = response.json()["data"]
    assert customer == {**data["customer"], "avatar_url": f"{AUTH}/avatar"}
    _assert_private(response)
    assert len(fake_object_storage.put_calls) == 1
    object_key = fake_object_storage.put_calls[0]
    stored = fake_object_storage.objects[object_key]
    assert stored != source
    assert fake_object_storage.stats[object_key].content_type == "image/jpeg"
    assert b"private-source-metadata" not in stored
    with Image.open(BytesIO(stored)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (512, 384)
        assert "exif" not in decoded.info
    with app.state.session_factory() as session:
        persisted = session.get(MiniCustomer, customer["id"])
        assert persisted is not None
        assert persisted.avatar_object_key == object_key
        assert persisted.avatar_sha256 == hashlib.sha256(stored).hexdigest()
        assert persisted.avatar_size_bytes == len(stored)

    fetched = client.get(customer["avatar_url"], headers=_bearer(data))
    assert fetched.status_code == 200
    assert fetched.content == stored
    assert fetched.headers["content-type"] == "image/jpeg"
    _assert_private(fetched)
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert client.get(customer["avatar_url"]).status_code == 401
    assert (
        client.get(
            customer["avatar_url"], params={"access_token": data["access_token"]}
        ).status_code
        == 401
    )
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).json() == response.json()


def test_avatar_does_not_disclose_another_customers_image(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    fake_object_storage: FakeObjectStorage,
) -> None:
    first = _login(client, "first-customer-code")
    uploaded = _upload(client, first, _image_bytes(size=(8, 8)))
    assert uploaded.status_code == 200
    mini_provider.identities["other-customer-code"] = WechatIdentity(
        app_id=APP_ID, openid="other-openid"
    )
    other = _login(client, "other-customer-code")
    get_calls = len(fake_object_storage.get_calls)

    response = client.get(uploaded.json()["data"]["avatar_url"], headers=_bearer(other))

    assert response.status_code == 404
    assert len(fake_object_storage.get_calls) == get_calls
    assert other["customer"]["avatar_url"] is None


@pytest.mark.parametrize(
    ("filename", "payload", "content_type"),
    [
        ("corrupt.png", b"not a real image", "image/png"),
        (
            "avatar.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="8" height="8"/></svg>',
            "image/svg+xml",
        ),
        ("animation.png", _animated_png(), "image/png"),
        ("avatar.gif", _image_bytes("GIF", size=(8, 8)), "image/gif"),
        ("empty.png", b"", "image/png"),
    ],
    ids=["corrupt", "svg", "animated-png", "gif", "empty"],
)
def test_invalid_avatar_uploads_leave_profile_and_storage_unchanged(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    fake_object_storage: FakeObjectStorage,
    filename: str,
    payload: bytes,
    content_type: str,
) -> None:
    data = _login(client)
    response = client.post(
        f"{AUTH}/avatar",
        headers=_bearer(data),
        files={"file": (filename, payload, content_type)},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mini_avatar_invalid"
    assert fake_object_storage.put_calls == []
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).json() == {"data": data["customer"]}


def test_avatar_upload_enforces_input_byte_limit_before_storage(
    client: TestClient,
    app: FastAPI,
    mini_provider: FakeWechatAuthProvider,
    fake_object_storage: FakeObjectStorage,
) -> None:
    data = _login(client)
    limit = app.state.settings.mini_avatar_upload_max_bytes
    assert limit == 2 * 1024 * 1024

    response = _upload(client, data, b"x" * (limit + 1))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "mini_avatar_too_large"
    assert fake_object_storage.put_calls == []
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).json() == {"data": data["customer"]}


def test_avatar_replacement_persists_new_image_and_removes_old_object(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    fake_object_storage: FakeObjectStorage,
) -> None:
    data = _login(client)
    assert _upload(client, data, _image_bytes(size=(8, 8))).status_code == 200
    old_object = fake_object_storage.put_calls[-1]

    response = _upload(client, data, _image_bytes(size=(16, 8)))

    assert response.status_code == 200
    new_object = fake_object_storage.put_calls[-1]
    assert old_object != new_object
    assert old_object in fake_object_storage.delete_calls
    assert old_object not in fake_object_storage.objects
    assert (
        client.get(f"{AUTH}/avatar", headers=_bearer(data)).content
        == fake_object_storage.objects[new_object]
    )


def test_avatar_put_failure_does_not_overwrite_existing_profile(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    fake_object_storage: FakeObjectStorage,
) -> None:
    data = _login(client)
    initial = _upload(client, data, _image_bytes(size=(8, 8)))
    assert initial.status_code == 200
    old_object = fake_object_storage.put_calls[-1]
    fake_object_storage.fail_put = True

    failed = _upload(client, data, _image_bytes(size=(16, 8)))

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "mini_avatar_unavailable"
    assert "fake object storage" not in failed.text
    assert client.get(f"{AUTH}/me", headers=_bearer(data)).json() == initial.json()
    assert (
        client.get(f"{AUTH}/avatar", headers=_bearer(data)).content
        == fake_object_storage.objects[old_object]
    )
    assert old_object not in fake_object_storage.delete_calls


def test_avatar_get_failure_returns_safe_private_error(
    client: TestClient,
    mini_provider: FakeWechatAuthProvider,
    fake_object_storage: FakeObjectStorage,
) -> None:
    data = _login(client)
    assert _upload(client, data, _image_bytes(size=(8, 8))).status_code == 200
    fake_object_storage.fail_get = True

    response = client.get(f"{AUTH}/avatar", headers=_bearer(data))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "mini_avatar_unavailable"
    assert "fake object storage" not in response.text
    _assert_private(response)
