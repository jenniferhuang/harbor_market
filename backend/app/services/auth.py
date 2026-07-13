from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import PasswordManager
from app.models import User
from app.schemas.auth import RegisterRequest


class AuthService:
    def __init__(self, passwords: PasswordManager) -> None:
        self._passwords = passwords

    def register(self, session: Session, payload: RegisterRequest) -> User:
        existing = session.scalar(select(User.id).where(User.username == payload.username))
        if existing is not None:
            raise self._username_conflict()

        user = User(
            username=payload.username,
            password_hash=self._passwords.hash(payload.password.get_secret_value()),
        )
        session.add(user)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise self._username_conflict() from exc
        session.refresh(user)
        return user

    def authenticate(self, session: Session, username: str, password: str) -> User | None:
        user = session.scalar(select(User).where(User.username == username))
        password_hash = user.password_hash if user is not None else self._passwords.dummy_hash
        password_valid = self._passwords.verify(password_hash, password)

        if user is None or not user.is_active or not password_valid:
            return None

        if self._passwords.needs_rehash(user.password_hash):
            user.password_hash = self._passwords.hash(password)
        now = datetime.now(UTC)
        user.last_login_at = now
        user.updated_at = now
        session.commit()
        session.refresh(user)
        return user

    @staticmethod
    def get_active_user(session: Session, user_id: int) -> User | None:
        return session.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))

    @staticmethod
    def _username_conflict() -> ApiError:
        return ApiError(409, "username_unavailable", "That username is already registered")
