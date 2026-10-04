from __future__ import annotations

import hashlib
import re
import secrets
import unicodedata
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models.mini_customer import MiniCustomer, MiniSession

if TYPE_CHECKING:
    from app.wechat.auth import WechatIdentity

_TOKEN_PATTERN = re.compile(r"mini_[A-Za-z0-9_-]{43}\Z")
_IDENTITY_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")
_CODE_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")


class MiniAuthService:
    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def login(
        self,
        session: Session,
        identity: WechatIdentity,
        code: str,
        ttl_seconds: int,
    ) -> tuple[MiniCustomer, str, datetime]:
        if (
            not isinstance(identity.app_id, str)
            or not 1 <= len(identity.app_id) <= 64
            or _IDENTITY_PATTERN.fullmatch(identity.app_id) is None
            or not isinstance(identity.openid, str)
            or not 1 <= len(identity.openid) <= 128
            or _IDENTITY_PATTERN.fullmatch(identity.openid) is None
        ):
            raise ApiError(502, "mini_identity_invalid", "微信登录返回无效身份，请稍后重试")
        if not isinstance(code, str) or _CODE_PATTERN.fullmatch(code) is None:
            raise ApiError(400, "mini_login_code_invalid", "登录凭证无效，请重新登录")
        if type(ttl_seconds) is not int or ttl_seconds <= 0:
            raise ValueError("Mini Program session lifetime must be positive")

        now = self._now()
        expires_at = now + timedelta(seconds=ttl_seconds)
        code_hash = self._hash(code)
        if self._code_used(session, identity.app_id, code_hash):
            raise self._code_reused()

        # The identity and session are committed together. If two different codes
        # create the same identity concurrently, only the unique-constraint loser
        # rolls back and retries with the winning identity.
        for attempt in range(2):
            customer = session.scalar(
                select(MiniCustomer).where(
                    MiniCustomer.app_id == identity.app_id,
                    MiniCustomer.openid == identity.openid,
                )
            )
            creating_customer = customer is None
            if customer is None:
                customer = MiniCustomer(
                    app_id=identity.app_id,
                    openid=identity.openid,
                    nickname="微信用户",
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(customer)
            elif not customer.is_active:
                raise ApiError(403, "mini_customer_inactive", "用户已停用")

            customer.last_login_at = now
            customer.updated_at = now
            token = "mini_" + secrets.token_urlsafe(32)
            record = MiniSession(
                customer=customer,
                app_id=identity.app_id,
                token_hash=self._hash(token),
                login_code_hash=code_hash,
                expires_at=expires_at,
                created_at=now,
            )
            session.add(record)
            try:
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if self._code_used(session, identity.app_id, code_hash):
                    raise self._code_reused() from exc
                winning_customer = session.scalar(
                    select(MiniCustomer.id).where(
                        MiniCustomer.app_id == identity.app_id,
                        MiniCustomer.openid == identity.openid,
                    )
                )
                if attempt == 0 and creating_customer and winning_customer is not None:
                    continue
                raise
            session.refresh(customer)
            return customer, token, expires_at

        raise RuntimeError("Mini Program identity retry did not complete")  # pragma: no cover

    def authenticate(
        self,
        session: Session,
        token: str,
    ) -> tuple[MiniCustomer, MiniSession] | None:
        if not isinstance(token, str) or not _TOKEN_PATTERN.fullmatch(token):
            return None
        result = session.execute(
            select(MiniCustomer, MiniSession)
            .join(MiniSession, MiniSession.customer_id == MiniCustomer.id)
            .where(
                MiniSession.token_hash == self._hash(token),
                MiniSession.revoked_at.is_(None),
                MiniCustomer.is_active.is_(True),
            )
        ).one_or_none()
        if result is None:
            return None
        customer, record = result
        if self._utc(record.expires_at) <= self._now():
            return None
        return customer, record

    def logout(self, session: Session, record: MiniSession) -> None:
        # Conditional update preserves the first revocation timestamp even when
        # two authenticated requests log out the same session concurrently.
        session.execute(
            update(MiniSession)
            .where(MiniSession.id == record.id, MiniSession.revoked_at.is_(None))
            .values(revoked_at=self._now())
        )
        session.commit()
        session.refresh(record)

    def update_nickname(
        self,
        session: Session,
        customer: MiniCustomer,
        nickname: str,
    ) -> MiniCustomer:
        nickname = nickname.strip()
        if not 1 <= len(nickname) <= 64 or any(
            unicodedata.category(character) in {"Cc", "Cs"} for character in nickname
        ):
            raise ApiError(
                422,
                "mini_nickname_invalid",
                "昵称不能为空且不能超过64个字符，不能包含控制字符",
            )
        customer.nickname = nickname
        customer.updated_at = self._now()
        session.commit()
        session.refresh(customer)
        return customer

    def _now(self) -> datetime:
        return self._utc(self._clock())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        # SQLite drops timezone information from DateTime columns in unit tests.
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _code_used(session: Session, app_id: str, code_hash: str) -> bool:
        return (
            session.scalar(
                select(MiniSession.id).where(
                    MiniSession.app_id == app_id,
                    MiniSession.login_code_hash == code_hash,
                )
            )
            is not None
        )

    @staticmethod
    def _code_reused() -> ApiError:
        return ApiError(400, "mini_login_code_reused", "登录凭证已使用，请重新登录")
