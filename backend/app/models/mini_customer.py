from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class MiniCustomer(Base):
    """Mini Program customer identities, independent of browser/admin users."""

    __tablename__ = "mini_customers"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(64), nullable=False)
    openid: Mapped[str] = mapped_column(String(128), nullable=False)
    nickname: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="微信用户",
        server_default=text("'微信用户'"),
    )
    avatar_object_key: Mapped[str | None] = mapped_column(String(512))
    avatar_sha256: Mapped[str | None] = mapped_column(String(64))
    avatar_size_bytes: Mapped[int | None] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list[MiniSession]] = relationship(
        back_populates="customer",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("app_id", "openid", name="uq_mini_customers_app_openid"),
        CheckConstraint("length(app_id) BETWEEN 1 AND 64", name="app_id_length"),
        CheckConstraint("length(openid) BETWEEN 1 AND 128", name="openid_length"),
        CheckConstraint("length(trim(nickname)) BETWEEN 1 AND 64", name="nickname_length"),
        CheckConstraint(
            "(avatar_object_key IS NULL AND avatar_sha256 IS NULL AND avatar_size_bytes IS NULL) "
            "OR (avatar_object_key IS NOT NULL AND avatar_sha256 IS NOT NULL "
            "AND avatar_size_bytes IS NOT NULL AND length(avatar_object_key) BETWEEN 1 AND 512 "
            "AND length(avatar_sha256) = 64 AND avatar_size_bytes > 0)",
            name="avatar_metadata_complete",
        ),
    )


class MiniSession(Base):
    """Revocable opaque bearer sessions; plaintext tokens and codes are never stored."""

    __tablename__ = "mini_sessions"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("mini_customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    app_id: Mapped[str] = mapped_column(String(64), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    login_code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    customer: Mapped[MiniCustomer] = relationship(back_populates="sessions")

    __table_args__ = (
        UniqueConstraint("app_id", "login_code_hash", name="uq_mini_sessions_app_login_code"),
        CheckConstraint("length(app_id) BETWEEN 1 AND 64", name="app_id_length"),
        CheckConstraint("length(token_hash) = 64", name="token_hash_length"),
        CheckConstraint("length(login_code_hash) = 64", name="login_code_hash_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_mini_sessions_customer_created", "customer_id", "created_at"),
        Index("ix_mini_sessions_expires_at", "expires_at"),
    )
