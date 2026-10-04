from __future__ import annotations

import hashlib
from io import BytesIO
from typing import Any

import pytest
from conftest import FakeObjectStorage
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin
from sqlalchemy import select

from app.core.errors import ApiError
from app.models import Category, MiniCustomer, ObjectCleanupJob, ShopMedia, ShopProfile
from app.services.object_cleanup import retryable_cleanup_jobs, run_object_cleanup_jobs
from app.services.shop_store import prepare_shop_image, public_store, validate_shop_mp4
from app.wechat.auth import WechatIdentity

ADMIN = "/api/v1/admin/shop"
MINI = "/api/v1/mini/shop"


def _image(format_name: str = "PNG", size: tuple[int, int] = (32, 16)) -> bytes:
    output = BytesIO()
    options: dict[str, Any] = {}
    if format_name == "PNG":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", "secret-location-metadata")
        options["pnginfo"] = metadata
    Image.new("RGB", size, "green").save(output, format=format_name, **options)
    return output.getvalue()


def _box(kind: bytes, body: bytes) -> bytes:
    return (len(body) + 8).to_bytes(4, "big") + kind + body


def _mp4(
    *,
    duration: int = 1000,
    track_duration: int | None = None,
    tkhd_duration: int | None = None,
    codec: bytes = b"avc1",
) -> bytes:
    # Structural fixture: the validator deliberately does not decode media samples.
    mvhd = b"\0" * 12 + (1000).to_bytes(4, "big") + duration.to_bytes(4, "big") + b"\0" * 80
    track_duration = duration if track_duration is None else track_duration
    mdhd = b"\0" * 12 + (1000).to_bytes(4, "big") + track_duration.to_bytes(4, "big") + b"\0" * 4
    visual = b"\0" * 24 + (32).to_bytes(2, "big") + (16).to_bytes(2, "big") + b"\0" * 50
    config = _box(b"avcC", b"\x01\x64\0\x1f\xff\xe1\0")
    stsd = _box(b"stsd", b"\0" * 4 + (1).to_bytes(4, "big") + _box(codec, visual + config))
    handler = _box(b"hdlr", b"\0" * 8 + b"vide" + b"\0" * 12)
    mdia = _box(b"mdia", _box(b"mdhd", mdhd) + handler + _box(b"minf", _box(b"stbl", stsd)))
    tkhd = (
        b""
        if tkhd_duration is None
        else _box(b"tkhd", b"\0" * 20 + tkhd_duration.to_bytes(4, "big") + b"\0" * 60)
    )
    movie = _box(b"moov", _box(b"mvhd", mvhd) + _box(b"trak", tkhd + mdia))
    return _box(b"ftyp", b"isom\0\0\0\0isommp42") + movie + _box(b"mdat", b"sample-data")


def _mini(app: FastAPI, identity: str = "shop-owner") -> tuple[int, dict[str, str]]:
    with app.state.session_factory() as session:
        customer, token, _ = app.state.mini_auth_service.login(
            session,
            WechatIdentity(app_id="wx-shop-test", openid=identity),
            identity,
            app.state.settings.mini_session_ttl_seconds,
        )
        return customer.id, {"Authorization": f"Bearer {token}"}


def _upload(
    client: TestClient, kind: str = "carousel", payload: bytes | None = None, **fields: Any
) -> Any:
    return client.post(
        f"{ADMIN}/media",
        data={"kind": kind, **fields},
        files={
            "file": (
                "untrusted-name.bin",
                payload if payload is not None else _image(),
                "application/octet-stream",
            )
        },
    )


def test_default_store_read_has_no_db_write_or_owner_disclosure(
    admin_client: TestClient, app: FastAPI
) -> None:
    response = admin_client.get(f"{ADMIN}/store")
    assert response.status_code == 200
    assert response.json()["data"] == {
        "name": "港湾集市",
        "phone": "",
        "address": "",
        "latitude": None,
        "longitude": None,
        "announcement_image_url": None,
        "owner_customer_id": None,
    }
    assert response.headers["cache-control"] == "private, no-store"
    with app.state.session_factory() as session:
        assert session.get(ShopProfile, 1) is None
        assert "owner_customer_id" not in public_store(session)


def test_admin_assignment_and_owner_patch_use_real_mini_identity(
    admin_client: TestClient, app: FastAPI, fake_object_storage: FakeObjectStorage
) -> None:
    owner, bearer = _mini(app)
    other, other_bearer = _mini(app, "other-customer")
    assert other != owner
    assert admin_client.get(f"{MINI}/me").status_code == 401
    assert admin_client.get(f"{MINI}/me", headers=bearer).json() == {
        "data": {"can_manage_store": False}
    }
    assigned = admin_client.patch(f"{ADMIN}/store", json={"owner_customer_id": owner})
    assert assigned.status_code == 200
    assert assigned.json()["data"]["owner_customer_id"] == owner
    assert admin_client.get(f"{MINI}/me", headers=bearer).json() == {
        "data": {"can_manage_store": True}
    }
    for path in ("/store",):
        assert admin_client.get(f"{MINI}{path}", headers=other_bearer).status_code == 403
    rejected = admin_client.post(
        f"{MINI}/store/announcement",
        headers=other_bearer,
        files={"file": ("picture.png", _image(), "image/png")},
    )
    assert rejected.status_code == 403
    assert fake_object_storage.put_calls == []
    assert (
        admin_client.patch(
            f"{MINI}/store", headers=bearer, json={"owner_customer_id": other}
        ).status_code
        == 422
    )
    response = admin_client.patch(
        f"{MINI}/store",
        headers=bearer,
        json={
            "name": "新门店",
            "phone": "123456",
            "address": "杭州",
            "latitude": 30.2,
            "longitude": 120.2,
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["name"] == "新门店"
    assert "owner_customer_id" not in response.json()["data"]
    assert response.headers["cache-control"] == "private, no-store"
    assert admin_client.get(f"{MINI}/store", headers=bearer).json() == response.json()


@pytest.mark.parametrize(
    "payload",
    [
        {"name": None},
        {"name": " "},
        {"phone": "x" * 41},
        {"address": "x" * 501},
        {"latitude": 91},
        {"longitude": -181},
        {"owner_customer_id": 2**31},
        {"owner_customer_id": True},
        {"unknown": "field"},
    ],
)
def test_store_rejects_invalid_fields(admin_client: TestClient, payload: dict[str, Any]) -> None:
    assert admin_client.patch(f"{ADMIN}/store", json=payload).status_code == 422


def test_admin_rejects_missing_or_inactive_owner_and_can_clear_owner(
    admin_client: TestClient, app: FastAPI
) -> None:
    assert (
        admin_client.patch(f"{ADMIN}/store", json={"owner_customer_id": 12345}).status_code == 400
    )
    customer_id, bearer = _mini(app)
    assert (
        admin_client.patch(f"{ADMIN}/store", json={"owner_customer_id": customer_id}).status_code
        == 200
    )
    assert (
        admin_client.patch(f"{ADMIN}/store", json={"owner_customer_id": None}).json()["data"][
            "owner_customer_id"
        ]
        is None
    )
    assert admin_client.get(f"{MINI}/store", headers=bearer).status_code == 403
    with app.state.session_factory() as session:
        customer = session.get(MiniCustomer, customer_id)
        assert customer is not None
        customer.is_active = False
        session.commit()
    assert (
        admin_client.patch(f"{ADMIN}/store", json={"owner_customer_id": customer_id}).status_code
        == 400
    )


def test_admin_media_requires_cookie_admin_and_same_origin(
    client: TestClient, admin_client: TestClient, fake_object_storage: FakeObjectStorage
) -> None:
    client.cookies.clear()
    assert _upload(client).status_code == 401
    assert fake_object_storage.put_calls == []


def test_admin_rejects_cross_origin_mutations(
    admin_client: TestClient, fake_object_storage: FakeObjectStorage
) -> None:
    assert (
        admin_client.patch(
            f"{ADMIN}/store", json={"name": "forged"}, headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert (
        admin_client.post(
            f"{ADMIN}/media",
            data={"kind": "carousel"},
            files={"file": ("picture.png", _image(), "image/png")},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    assert fake_object_storage.put_calls == []


@pytest.mark.parametrize("format_name", ["JPEG", "PNG", "WEBP"])
def test_image_upload_reencodes_metadata_and_exposes_only_safe_public_url(
    admin_client: TestClient,
    app: FastAPI,
    fake_object_storage: FakeObjectStorage,
    format_name: str,
) -> None:
    response = _upload(admin_client, payload=_image(format_name, (3072, 1024)), title="展示图")
    assert response.status_code == 201, response.text
    media = response.json()["data"]
    assert set(media) == {
        "id",
        "kind",
        "category_id",
        "title",
        "media_type",
        "url",
        "sort_order",
        "is_active",
    }
    assert media["url"] == f"/api/v1/shop/media/{media['id']}"
    key = fake_object_storage.put_calls[-1]
    stored = fake_object_storage.objects[key]
    assert b"secret-location-metadata" not in stored
    with Image.open(BytesIO(stored)) as image:
        assert image.format == "JPEG"
        assert image.size == (2048, 683)
        assert "exif" not in image.info
    with app.state.session_factory() as session:
        row = session.get(ShopMedia, media["id"])
        assert row is not None
        assert row.sha256 == hashlib.sha256(stored).hexdigest()
        assert row.size_bytes == len(stored)
    fetched = admin_client.get(media["url"])
    assert fetched.content == stored
    assert fetched.headers["content-type"] == "image/jpeg"
    assert fetched.headers["content-security-policy"] == "sandbox"
    assert fetched.headers["x-content-type-options"] == "nosniff"
    assert fetched.headers["cache-control"] == "public, max-age=0, must-revalidate"
    assert admin_client.get(f"{ADMIN}/media").json() == {"data": [media]}


@pytest.mark.parametrize(
    "payload", [b"", b"<html><script>alert(1)</script></html>", b"<svg></svg>", _image("GIF")]
)
def test_spoofed_image_uploads_do_not_write_storage(
    admin_client: TestClient, fake_object_storage: FakeObjectStorage, payload: bytes
) -> None:
    assert _upload(admin_client, payload=payload).status_code == 400
    assert fake_object_storage.put_calls == []


def test_image_and_video_byte_limits_apply_before_storage(
    admin_client: TestClient, fake_object_storage: FakeObjectStorage
) -> None:
    assert _upload(admin_client, payload=b"x" * (5 * 1024 * 1024 + 1)).status_code == 413
    assert (
        _upload(admin_client, payload=b"\0\0\0\x18ftyp" + b"x" * (10 * 1024 * 1024)).status_code
        == 413
    )
    assert fake_object_storage.put_calls == []


def test_animated_and_oversized_pixel_images_are_rejected() -> None:
    output = BytesIO()
    first = Image.new("RGB", (8, 8), "red")
    first.save(
        output,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")],
        duration=100,
        loop=0,
    )
    with pytest.raises(ApiError, match="静态"):
        prepare_shop_image(output.getvalue())
    with pytest.raises(ApiError, match="1600万"):
        prepare_shop_image(_image(size=(4001, 4000)))


def test_category_upload_scope_replacement_and_inactive_category_hiding(
    admin_client: TestClient, app: FastAPI, fake_object_storage: FakeObjectStorage
) -> None:
    assert _upload(admin_client, kind="category").status_code == 422
    assert _upload(admin_client, kind="category", category_id=12345).status_code == 404
    assert _upload(admin_client, kind="announcement", category_id=1).status_code == 422
    with app.state.session_factory() as session:
        category = Category(code="DRINKS", name="饮品")
        session.add(category)
        session.commit()
        category_id = category.id
    first = _upload(admin_client, kind="category", category_id=category_id).json()["data"]
    old_key = fake_object_storage.put_calls[-1]
    second = _upload(
        admin_client, kind="category", category_id=category_id, payload=_image(size=(40, 20))
    )
    assert second.status_code == 201
    assert first["url"] != second.json()["data"]["url"]
    assert old_key in fake_object_storage.delete_calls
    assert admin_client.get(first["url"]).status_code == 404
    with app.state.session_factory() as session:
        category = session.get(Category, category_id)
        assert category is not None
        category.is_active = False
        session.commit()
    assert admin_client.get(second.json()["data"]["url"]).status_code == 404


def test_owner_announcement_replaces_row_and_failed_cleanup_is_retryable(
    admin_client: TestClient, app: FastAPI, fake_object_storage: FakeObjectStorage
) -> None:
    owner, bearer = _mini(app)
    admin_client.patch(f"{ADMIN}/store", json={"owner_customer_id": owner})
    first = admin_client.post(
        f"{MINI}/store/announcement",
        headers=bearer,
        files={"file": ("a.png", _image(), "image/png")},
    )
    assert first.status_code == 200
    old_url = first.json()["data"]["announcement_image_url"]
    old_key = fake_object_storage.put_calls[-1]
    fake_object_storage.fail_delete = True
    second = admin_client.post(
        f"{MINI}/store/announcement",
        headers=bearer,
        files={"file": ("a.png", _image(size=(40, 20)), "image/png")},
    )
    assert second.status_code == 200
    new_key = fake_object_storage.put_calls[-1]
    assert old_key != new_key
    assert admin_client.get(old_url).status_code == 404
    assert admin_client.get(second.json()["data"]["announcement_image_url"]).status_code == 200
    with app.state.session_factory() as session:
        job = session.scalar(
            select(ObjectCleanupJob).where(ObjectCleanupJob.reason == "shop_media_replaced")
        )
        assert job is not None and job.status == "failed" and job.not_before is not None
        fake_object_storage.fail_delete = False
        jobs = retryable_cleanup_jobs(session, job_id=job.id, force_failed=True)
        assert run_object_cleanup_jobs(session, fake_object_storage, jobs) == []
        assert old_key not in fake_object_storage.objects
        assert new_key in fake_object_storage.objects


def test_upload_failure_preserves_prior_announcement_and_cleans_partial_object(
    admin_client: TestClient, fake_object_storage: FakeObjectStorage, monkeypatch: Any
) -> None:
    first = _upload(admin_client, kind="announcement").json()["data"]
    old_key = fake_object_storage.put_calls[-1]
    original_put = fake_object_storage.put

    def partial_put(*args: Any, **kwargs: Any) -> None:
        original_put(*args, **kwargs)
        raise RuntimeError("secret storage error")

    monkeypatch.setattr(fake_object_storage, "put", partial_put)
    failure = _upload(admin_client, kind="announcement")
    assert failure.status_code == 503
    assert "secret storage error" not in failure.text
    new_key = fake_object_storage.put_calls[-1]
    assert new_key not in fake_object_storage.objects
    assert old_key in fake_object_storage.objects
    assert admin_client.get(first["url"]).status_code == 200
    assert (
        admin_client.get(f"{ADMIN}/store").json()["data"]["announcement_image_url"] == first["url"]
    )


def test_patch_activation_conflicts_and_delete_preserve_cleanup(
    admin_client: TestClient, app: FastAPI, fake_object_storage: FakeObjectStorage
) -> None:
    active = _upload(admin_client, kind="announcement").json()["data"]
    inactive = _upload(admin_client, kind="announcement", is_active="false").json()["data"]
    assert admin_client.get(inactive["url"]).status_code == 404
    assert (
        admin_client.patch(f"{ADMIN}/media/{inactive['id']}", json={"is_active": True}).status_code
        == 409
    )
    assert (
        admin_client.patch(f"{ADMIN}/media/{active['id']}", json={"kind": "carousel"}).status_code
        == 422
    )
    edited = admin_client.patch(
        f"{ADMIN}/media/{active['id']}",
        json={"title": "新公告", "sort_order": -1, "is_active": False},
    )
    assert edited.status_code == 200
    assert admin_client.get(active["url"]).status_code == 404
    assert (
        admin_client.patch(f"{ADMIN}/media/{inactive['id']}", json={"is_active": True}).status_code
        == 200
    )
    fake_object_storage.fail_delete = True
    deleted = admin_client.delete(f"{ADMIN}/media/{inactive['id']}")
    assert deleted.json() == {"data": {"deleted": True}}
    assert admin_client.get(inactive["url"]).status_code == 404
    with app.state.session_factory() as session:
        assert session.get(ShopMedia, inactive["id"]) is None
        job = session.scalar(
            select(ObjectCleanupJob).where(ObjectCleanupJob.reason == "shop_media_deleted")
        )
        assert job is not None and job.status == "failed"


@pytest.mark.parametrize(
    "payload",
    [
        _mp4(duration=60001),
        _mp4(duration=0),
        _mp4(codec=b"html"),
        b"\0\0\0\x18ftypisom\0\0\0\0isommp42",
        _mp4()[:-1],
        _mp4() + b"<html></html>",
    ],
)
def test_invalid_mp4_containers_are_rejected(payload: bytes) -> None:
    with pytest.raises(ApiError, match="MP4"):
        validate_shop_mp4(payload)


@pytest.mark.parametrize("track_duration", [61000, 10000])
def test_short_movie_header_cannot_hide_long_or_inconsistent_video_track(
    track_duration: int,
) -> None:
    with pytest.raises(ApiError, match="60秒"):
        validate_shop_mp4(_mp4(duration=1000, track_duration=track_duration))


def test_track_header_duration_is_bounded() -> None:
    with pytest.raises(ApiError, match="60秒"):
        validate_shop_mp4(_mp4(tkhd_duration=61000))


@pytest.mark.parametrize("kind", ["category", "announcement"])
def test_noncarousel_video_uploads_are_rejected(
    admin_client: TestClient, fake_object_storage: FakeObjectStorage, kind: str
) -> None:
    fields = {"category_id": 1} if kind == "category" else {}
    assert _upload(admin_client, kind=kind, payload=_mp4(), **fields).status_code == 400
    assert fake_object_storage.put_calls == []


def test_mp4_public_single_ranges_and_unsatisfiable_ranges(admin_client: TestClient) -> None:
    payload = _mp4()
    uploaded = _upload(admin_client, payload=payload)
    assert uploaded.status_code == 201, uploaded.text
    media = uploaded.json()["data"]
    assert media["media_type"] == "video"
    full = admin_client.get(media["url"])
    assert full.content == payload
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-type"] == "video/mp4"
    for value, start, end in [
        ("bytes=0-9", 0, 9),
        ("bytes=10-", 10, len(payload) - 1),
        ("bytes=-8", len(payload) - 8, len(payload) - 1),
        ("bytes=0-99999", 0, len(payload) - 1),
    ]:
        response = admin_client.get(media["url"], headers={"Range": value})
        assert response.status_code == 206
        assert response.content == payload[start : end + 1]
        assert response.headers["content-range"] == f"bytes {start}-{end}/{len(payload)}"
        assert int(response.headers["content-length"]) == end - start + 1
    for value in ["bytes=99999-", "bytes=4-2", "bytes=-0", "bytes=0-1,3-4", "bytes=-"]:
        response = admin_client.get(media["url"], headers={"Range": value})
        assert response.status_code == 416
        assert response.headers["content-range"] == f"bytes */{len(payload)}"


def test_public_media_checks_storage_length_and_hash(
    admin_client: TestClient, fake_object_storage: FakeObjectStorage
) -> None:
    media = _upload(admin_client).json()["data"]
    key = fake_object_storage.put_calls[-1]
    original = fake_object_storage.objects[key]
    fake_object_storage.objects[key] = b"x" * len(original)
    assert admin_client.get(media["url"]).status_code == 503
    fake_object_storage.objects[key] = original + b"x"
    assert admin_client.get(media["url"]).status_code == 503


def test_configured_image_limit_also_bounds_reencoded_output(
    admin_client: TestClient, app: FastAPI, fake_object_storage: FakeObjectStorage
) -> None:
    payload = _image()
    assert len(prepare_shop_image(payload)) > len(payload)
    app.state.settings = app.state.settings.model_copy(
        update={
            "shop_image_upload_max_bytes": len(payload),
        }
    )
    assert _upload(admin_client, payload=payload).status_code == 413
    assert fake_object_storage.put_calls == []


def test_independent_image_and_video_upload_limits(
    admin_client: TestClient, app: FastAPI, fake_object_storage: FakeObjectStorage
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"shop_video_upload_max_bytes": 1})
    assert _upload(admin_client, payload=_image()).status_code == 201
    previous_puts = list(fake_object_storage.put_calls)
    assert _upload(admin_client, payload=_mp4()).status_code == 413
    assert fake_object_storage.put_calls == previous_puts


def test_admin_media_list_uses_bounded_pagination(admin_client: TestClient) -> None:
    first = _upload(admin_client, title="first").json()["data"]
    second = _upload(admin_client, title="second").json()["data"]
    assert admin_client.get(f"{ADMIN}/media", params={"page": 1, "page_size": 1}).json() == {
        "data": [first]
    }
    assert admin_client.get(f"{ADMIN}/media", params={"page": 2, "page_size": 1}).json() == {
        "data": [second]
    }
    for params in [{"page": 0}, {"page": 1_000_001}, {"page_size": 101}]:
        assert admin_client.get(f"{ADMIN}/media", params=params).status_code == 422


@pytest.mark.parametrize("media_id", [0, 2**31, 10**100])
def test_media_path_ids_are_bounded(admin_client: TestClient, media_id: int) -> None:
    assert admin_client.get(f"/api/v1/shop/media/{media_id}").status_code == 422
    assert admin_client.patch(f"{ADMIN}/media/{media_id}", json={"title": "x"}).status_code == 422
    assert admin_client.delete(f"{ADMIN}/media/{media_id}").status_code == 422


def test_active_carousel_quota_applies_to_upload_and_activation(
    admin_client: TestClient, app: FastAPI, fake_object_storage: FakeObjectStorage
) -> None:
    with app.state.session_factory() as session:
        for index in range(21):
            session.add(
                ShopMedia(
                    kind="carousel",
                    title="",
                    media_type="image",
                    object_key=f"shop/test/{index}.jpg",
                    mime_type="image/jpeg",
                    size_bytes=1,
                    sha256="0" * 64,
                    is_active=index < 20,
                )
            )
        session.commit()
        inactive = session.scalar(select(ShopMedia).where(ShopMedia.is_active.is_(False)))
        assert inactive is not None
        inactive_id = inactive.id
    assert _upload(admin_client).status_code == 409
    assert fake_object_storage.put_calls == []
    assert (
        admin_client.patch(f"{ADMIN}/media/{inactive_id}", json={"is_active": True}).status_code
        == 409
    )
    assert len(admin_client.get(f"{ADMIN}/media").json()["data"]) == 20
