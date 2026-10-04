from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.base import Base
from app.models import MiniCustomer, MiniSession
from app.services.mini_auth import MiniAuthService

NOW = datetime(2026, 10, 4, 8, 0, tzinfo=UTC)


@pytest.fixture
def mini_session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def identity(app_id: str = "wx-app-one", openid: str = "openid-one") -> SimpleNamespace:
    return SimpleNamespace(app_id=app_id, openid=openid)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_login_stores_only_hashed_credentials_and_has_separate_customer_identity(
    mini_session: Session,
) -> None:
    service = MiniAuthService(clock=lambda: NOW)

    customer, token, expires_at = service.login(mini_session, identity(), "code-one", 3600)

    record = mini_session.scalar(select(MiniSession))
    assert record is not None
    assert token.startswith("mini_") and len(token) == 48
    assert record.token_hash == digest(token)
    assert record.login_code_hash == digest("code-one")
    assert token not in record.__dict__.values()
    assert "code-one" not in record.__dict__.values()
    assert customer.nickname == "微信用户"
    assert customer.is_active is True
    assert customer.last_login_at == NOW.replace(tzinfo=None)
    assert expires_at == NOW + timedelta(hours=1)
    assert expires_at.tzinfo is UTC
    assert service.authenticate(mini_session, token) == (customer, record)
    assert {
        column["name"] for column in inspect(mini_session.bind).get_columns("mini_customers")
    } == {
        "id",
        "app_id",
        "openid",
        "nickname",
        "avatar_object_key",
        "avatar_sha256",
        "avatar_size_bytes",
        "is_active",
        "created_at",
        "updated_at",
        "last_login_at",
    }
    assert {
        fk["referred_table"] for fk in inspect(mini_session.bind).get_foreign_keys("mini_sessions")
    } == {"mini_customers"}


def test_repeat_login_reuses_identity_with_a_fresh_opaque_session(mini_session: Session) -> None:
    times = [NOW]
    service = MiniAuthService(clock=lambda: times[0])
    customer, first_token, _ = service.login(mini_session, identity(), "first-code", 3600)
    customer.nickname = "海港访客"
    mini_session.commit()
    times[0] += timedelta(minutes=1)

    again, second_token, _ = service.login(mini_session, identity(), "second-code", 3600)

    assert again.id == customer.id
    assert again.nickname == "海港访客"
    assert again.last_login_at == times[0].replace(tzinfo=None)
    assert first_token != second_token
    assert mini_session.scalar(select(func.count()).select_from(MiniCustomer)) == 1
    assert mini_session.scalar(select(func.count()).select_from(MiniSession)) == 2
    assert service.authenticate(mini_session, first_token) is not None
    assert service.authenticate(mini_session, second_token) is not None


def test_login_code_is_one_time_even_after_logout_and_expiry(mini_session: Session) -> None:
    times = [NOW]
    service = MiniAuthService(clock=lambda: times[0])
    _, token, _ = service.login(mini_session, identity(), "one-time-code", 1)
    authenticated = service.authenticate(mini_session, token)
    assert authenticated is not None
    service.logout(mini_session, authenticated[1])
    times[0] += timedelta(days=1)

    with pytest.raises(ApiError) as error:
        service.login(mini_session, identity(), "one-time-code", 3600)

    assert error.value.status_code == 400
    assert error.value.code == "mini_login_code_reused"
    assert mini_session.scalar(select(func.count()).select_from(MiniSession)) == 1


def test_app_ids_scope_identities_and_code_replay_detection(mini_session: Session) -> None:
    service = MiniAuthService(clock=lambda: NOW)

    first, _, _ = service.login(mini_session, identity("wx-first"), "shared-code", 3600)
    second, _, _ = service.login(mini_session, identity("wx-second"), "shared-code", 3600)

    assert first.id != second.id
    assert first.openid == second.openid
    assert mini_session.scalar(select(func.count()).select_from(MiniCustomer)) == 2
    assert mini_session.scalar(select(func.count()).select_from(MiniSession)) == 2


@pytest.mark.parametrize(
    "token", [None, "", "mini_invalid", "browser.cookie.signature", "x" * 10000]
)
def test_malformed_and_browser_tokens_cannot_authenticate(
    mini_session: Session, token: str | None
) -> None:
    service = MiniAuthService(clock=lambda: NOW)

    assert service.authenticate(mini_session, token) is None


@pytest.mark.parametrize(
    "app_id,openid",
    [
        ("", "openid-one"),
        ("wx-invalid-app ", "openid-one"),
        ("a" * 65, "openid-one"),
        ("wx-app-one", ""),
        ("wx-app-one", "\u0000malformed"),
        ("wx-app-one", "a" * 129),
        ("wx-app-one", "微信用户"),
        ("wx-app-one", None),
    ],
)
def test_malformed_injected_provider_identity_is_rejected_before_persistence(
    mini_session: Session, app_id: str, openid: str | None
) -> None:
    service = MiniAuthService(clock=lambda: NOW)

    with pytest.raises(ApiError) as error:
        service.login(mini_session, identity(app_id, openid), "unused-code", 3600)

    assert error.value.status_code == 502
    assert error.value.code == "mini_identity_invalid"
    assert mini_session.scalar(select(func.count()).select_from(MiniCustomer)) == 0
    assert mini_session.scalar(select(func.count()).select_from(MiniSession)) == 0


@pytest.mark.parametrize("code", [None, "", "invalid code", "a" * 257, "code\ud800"])
def test_invalid_code_is_rejected_before_persistence(
    mini_session: Session, code: str | None
) -> None:
    service = MiniAuthService(clock=lambda: NOW)

    with pytest.raises(ApiError) as error:
        service.login(mini_session, identity(), code, 3600)

    assert error.value.status_code == 400
    assert error.value.code == "mini_login_code_invalid"
    assert mini_session.scalar(select(func.count()).select_from(MiniCustomer)) == 0


def test_expiry_boundary_unknown_tokens_and_inactive_customers_are_rejected(
    mini_session: Session,
) -> None:
    times = [NOW]
    service = MiniAuthService(clock=lambda: times[0])
    customer, token, expires_at = service.login(mini_session, identity(), "expiry-code", 1)
    assert service.authenticate(mini_session, "mini_" + "a" * 43) is None
    times[0] = expires_at - timedelta(microseconds=1)
    assert service.authenticate(mini_session, token) is not None
    times[0] = expires_at
    assert service.authenticate(mini_session, token) is None
    times[0] = NOW
    customer.is_active = False
    mini_session.commit()

    assert service.authenticate(mini_session, token) is None
    with pytest.raises(ApiError) as error:
        service.login(mini_session, identity(), "inactive-code", 3600)
    assert error.value.code == "mini_customer_inactive"
    assert error.value.status_code == 403
    assert mini_session.scalar(select(func.count()).select_from(MiniSession)) == 1


def test_logout_is_idempotent_and_only_revokes_the_given_session(mini_session: Session) -> None:
    times = [NOW]
    service = MiniAuthService(clock=lambda: times[0])
    _, first_token, _ = service.login(mini_session, identity(), "logout-one", 3600)
    _, second_token, _ = service.login(mini_session, identity(), "logout-two", 3600)
    authenticated = service.authenticate(mini_session, first_token)
    assert authenticated is not None
    record = authenticated[1]

    service.logout(mini_session, record)
    first_revocation = record.revoked_at
    times[0] += timedelta(seconds=10)
    service.logout(mini_session, record)

    assert record.revoked_at == first_revocation == NOW.replace(tzinfo=None)
    assert service.authenticate(mini_session, first_token) is None
    assert service.authenticate(mini_session, second_token) is not None


def test_clock_can_supply_an_offset_or_naive_datetime(mini_session: Session) -> None:
    shanghai_time = NOW.astimezone(timezone(timedelta(hours=8)))
    service = MiniAuthService(clock=lambda: shanghai_time)
    _, token, expires_at = service.login(mini_session, identity(), "offset-code", 3600)
    assert expires_at == NOW + timedelta(hours=1)
    assert expires_at.tzinfo is UTC
    service = MiniAuthService(clock=lambda: NOW.replace(tzinfo=None))
    assert service.authenticate(mini_session, token) is not None


def test_nickname_update_trims_and_preserves_customer_identity(mini_session: Session) -> None:
    service = MiniAuthService(clock=lambda: NOW)
    customer, _, _ = service.login(mini_session, identity(), "nickname-code", 3600)

    updated = service.update_nickname(mini_session, customer, "  海港访客👋  ")

    assert updated.id == customer.id
    assert updated.nickname == "海港访客👋"
    assert updated.openid == "openid-one"
    assert updated.updated_at == NOW.replace(tzinfo=None)


@pytest.mark.parametrize("nickname", [" ", "a" * 65, "name\nother", "name\x00", "name\ud800"])
def test_nickname_rejects_empty_overlong_and_control_characters(
    mini_session: Session, nickname: str
) -> None:
    service = MiniAuthService(clock=lambda: NOW)
    customer, _, _ = service.login(mini_session, identity(), "nickname-code", 3600)

    with pytest.raises(ApiError) as error:
        service.update_nickname(mini_session, customer, nickname)

    assert error.value.status_code == 422
    assert error.value.code == "mini_nickname_invalid"
    assert customer.nickname == "微信用户"


def test_nickname_boundary_counts_64_unicode_codepoints_including_emoji(
    mini_session: Session,
) -> None:
    service = MiniAuthService(clock=lambda: NOW)
    customer, _, _ = service.login(mini_session, identity(), "nickname-boundary-code", 3600)
    nickname = "👋" * 64

    service.update_nickname(mini_session, customer, nickname)

    assert customer.nickname == nickname
    assert len(customer.nickname) == 64
    with pytest.raises(ApiError) as error:
        service.update_nickname(mini_session, customer, "👋" * 65)
    assert error.value.status_code == 422
    assert error.value.code == "mini_nickname_invalid"
    assert customer.nickname == nickname


def test_identity_constraint_race_reloads_winner_and_creates_one_customer(
    mini_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MiniAuthService(clock=lambda: NOW)
    original_commit = mini_session.commit
    commit_calls = 0

    def concurrent_commit() -> None:
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 1:
            with Session(mini_session.bind) as competitor:
                competitor.add(
                    MiniCustomer(
                        app_id="wx-app-one",
                        openid="openid-one",
                        nickname="并发登录用户",
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                competitor.commit()
        original_commit()

    monkeypatch.setattr(mini_session, "commit", concurrent_commit)

    customer, token, _ = service.login(mini_session, identity(), "racing-code", 3600)

    assert commit_calls == 2
    assert customer.nickname == "并发登录用户"
    assert mini_session.scalar(select(func.count()).select_from(MiniCustomer)) == 1
    assert mini_session.scalar(select(func.count()).select_from(MiniSession)) == 1
    assert service.authenticate(mini_session, token) is not None


def test_code_constraint_closes_concurrent_replay_window(
    mini_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MiniAuthService(clock=lambda: NOW)
    customer, _, _ = service.login(mini_session, identity(), "initial-code", 3600)
    original_commit = mini_session.commit

    def concurrent_commit() -> None:
        with Session(mini_session.bind) as competitor:
            competitor.add(
                MiniSession(
                    customer_id=customer.id,
                    app_id="wx-app-one",
                    token_hash=digest("winning-token"),
                    login_code_hash=digest("racing-code"),
                    created_at=NOW,
                    expires_at=NOW + timedelta(hours=1),
                )
            )
            competitor.commit()
        original_commit()

    monkeypatch.setattr(mini_session, "commit", concurrent_commit)

    with pytest.raises(ApiError) as error:
        service.login(mini_session, identity(), "racing-code", 3600)

    assert error.value.code == "mini_login_code_reused"
    assert mini_session.scalar(select(func.count()).select_from(MiniCustomer)) == 1
    assert mini_session.scalar(select(func.count()).select_from(MiniSession)) == 2


@pytest.mark.parametrize(
    "metadata",
    [
        {"avatar_object_key": "avatars/one.png"},
        {"avatar_object_key": "avatars/one.png", "avatar_sha256": "a" * 64},
        {
            "avatar_object_key": "avatars/one.png",
            "avatar_sha256": "a" * 64,
            "avatar_size_bytes": 0,
        },
        {
            "avatar_object_key": "avatars/one.png",
            "avatar_sha256": "a" * 63,
            "avatar_size_bytes": 1,
        },
    ],
)
def test_avatar_metadata_constraint_rejects_partial_or_invalid_trios(
    mini_session: Session, metadata: dict[str, object]
) -> None:
    mini_session.add(MiniCustomer(app_id="wx-app-one", openid="openid-one", **metadata))

    with pytest.raises(IntegrityError):
        mini_session.commit()
    mini_session.rollback()


def test_avatar_metadata_constraint_accepts_an_empty_or_complete_trio(
    mini_session: Session,
) -> None:
    empty = MiniCustomer(app_id="wx-app-one", openid="no-avatar")
    complete = MiniCustomer(
        app_id="wx-app-one",
        openid="with-avatar",
        avatar_object_key="mini/avatars/one.png",
        avatar_sha256="a" * 64,
        avatar_size_bytes=1024,
    )
    mini_session.add_all([empty, complete])

    mini_session.commit()

    assert mini_session.scalar(select(func.count()).select_from(MiniCustomer)) == 2
