from __future__ import annotations

import hashlib
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from itsdangerous import BadData, URLSafeTimedSerializer

from app.core.config import Settings


class PasswordManager:
    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
        )
        self.dummy_hash = self.hash("harbor-market-dummy-password")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (InvalidHashError, VerificationError, VerifyMismatchError):
            return False

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return False


class SessionCookieManager:
    def __init__(self, settings: Settings) -> None:
        self.cookie_name = settings.auth_cookie_name
        self.cookie_path = settings.auth_cookie_path
        self.cookie_domain = settings.auth_cookie_domain
        self.secure = settings.auth_cookie_secure
        self.samesite = settings.auth_cookie_samesite
        self.max_age = settings.auth_session_ttl_seconds
        self.salt = settings.auth_signing_salt
        self._serializer = URLSafeTimedSerializer(
            settings.auth_secret_key.get_secret_value(),
            salt=self.salt,
            signer_kwargs={"digest_method": hashlib.sha256},
        )

    def create(self, user_id: int) -> str:
        return self._serializer.dumps({"uid": user_id})

    def verify(self, token: str) -> int | None:
        try:
            payload: Any = self._serializer.loads(token, max_age=self.max_age)
        except BadData:
            return None
        if not isinstance(payload, dict):
            return None
        user_id = payload.get("uid")
        if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id < 1:
            return None
        return user_id
