from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

_PAYMENT_STATUSES = "'created', 'pending', 'succeeded', 'closed', 'failed'"


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    public_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    order_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_order_no: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="wechat_pay",
        server_default=text("'wechat_pay'"),
    )
    provider_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    provider_app_id: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="created",
        server_default=text("'created'"),
    )
    provider_state_raw: Mapped[str | None] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="CNY",
        server_default=text("'CNY'"),
    )
    description: Mapped[str] = mapped_column(String(127), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    prepay_id: Mapped[str | None] = mapped_column(String(128))
    prepay_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    provider_transaction_id: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_message: Mapped[str | None] = mapped_column(String(500))
    close_reason: Mapped[str | None] = mapped_column(String(64))
    review_required: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    review_reason: Mapped[str | None] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
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
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list[PaymentStateEvent]] = relationship(
        back_populates="payment_attempt",
        passive_deletes=True,
        order_by="PaymentStateEvent.id",
    )
    provider_events: Mapped[list[PaymentProviderEvent]] = relationship(
        back_populates="payment_attempt",
        passive_deletes=True,
    )

    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        CheckConstraint(f"status IN ({_PAYMENT_STATUSES})", name="status_allowed"),
        CheckConstraint("provider_mode IN ('mock', 'live')", name="provider_mode_allowed"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("currency = 'CNY'", name="currency_cny"),
        CheckConstraint(
            "length(trim(order_reference)) BETWEEN 6 AND 64",
            name="order_reference_length",
        ),
        CheckConstraint(
            "length(merchant_order_no) BETWEEN 6 AND 32",
            name="merchant_order_no_length",
        ),
        CheckConstraint("length(request_hash) = 64", name="request_hash_length"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "status <> 'pending' OR (prepay_id IS NOT NULL AND prepay_expires_at IS NOT NULL)",
            name="pending_has_prepay",
        ),
        CheckConstraint(
            "status <> 'succeeded' OR "
            "(provider_transaction_id IS NOT NULL AND paid_at IS NOT NULL)",
            name="success_has_transaction",
        ),
        CheckConstraint(
            "status <> 'closed' OR closed_at IS NOT NULL",
            name="closed_has_timestamp",
        ),
        CheckConstraint(
            "status <> 'failed' OR failed_at IS NOT NULL",
            name="failed_has_timestamp",
        ),
        UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_payment_attempts_owner_idempotency_key",
        ),
        UniqueConstraint(
            "provider",
            "provider_merchant_id",
            "merchant_order_no",
            name="uq_payment_attempts_provider_merchant_order",
        ),
        UniqueConstraint(
            "provider",
            "provider_transaction_id",
            name="uq_payment_attempts_provider_transaction",
        ),
        Index("ix_payment_attempts_owner_created", "owner_user_id", "created_at"),
        Index("ix_payment_attempts_order_created", "order_reference", "created_at"),
        Index("ix_payment_attempts_status_updated", "status", "updated_at"),
        Index(
            "uq_payment_attempts_one_active_order",
            "order_reference",
            unique=True,
            postgresql_where=text("status IN ('created', 'pending')"),
            sqlite_where=text("status IN ('created', 'pending')"),
        ),
    )


class PaymentStateEvent(Base):
    __tablename__ = "payment_state_events"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    payment_attempt_id: Mapped[int] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    reason_code: Mapped[str | None] = mapped_column(String(80))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    payment_attempt: Mapped[PaymentAttempt] = relationship(back_populates="events")

    __table_args__ = (
        CheckConstraint(
            "source IN ('api', 'provider_notification', 'provider_query', 'system')",
            name="source_allowed",
        ),
        CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_PAYMENT_STATUSES})",
            name="from_status_allowed",
        ),
        CheckConstraint(
            f"to_status IS NULL OR to_status IN ({_PAYMENT_STATUSES})",
            name="to_status_allowed",
        ),
        Index("ix_payment_state_events_attempt_created", "payment_attempt_id", "created_at"),
    )


class MockPaymentProviderRecord(Base):
    """Durable external-state simulator used only by non-production mock mode."""

    __tablename__ = "payment_mock_provider_records"

    merchant_order_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    app_id: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    prepay_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    prepay_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    prepay_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    client_parameters: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    trade_state: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="NOTPAY",
        server_default=text("'NOTPAY'"),
    )
    transaction_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    success_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    __table_args__ = (
        CheckConstraint("length(request_fingerprint) = 64", name="request_fingerprint_length"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("currency = 'CNY'", name="currency_cny"),
        CheckConstraint("prepay_generation > 0", name="prepay_generation_positive"),
        CheckConstraint(
            "trade_state IN ('NOTPAY', 'SUCCESS', 'CLOSED', 'PAYERROR', 'REFUND')",
            name="trade_state_allowed",
        ),
        CheckConstraint(
            "trade_state NOT IN ('SUCCESS', 'REFUND') OR "
            "(transaction_id IS NOT NULL AND success_time IS NOT NULL)",
            name="success_has_transaction",
        ),
        Index("ix_payment_mock_provider_state_updated", "trade_state", "updated_at"),
    )


class PaymentProviderEvent(Base):
    __tablename__ = "payment_provider_events"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_app_id: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    merchant_order_no: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_state_raw: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_transaction_id: Mapped[str | None] = mapped_column(String(64))
    provider_success_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL"),
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    signature_verified: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )
    processing_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="received",
        server_default=text("'received'"),
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_status_code: Mapped[int | None] = mapped_column(Integer)
    provider_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    payment_attempt: Mapped[PaymentAttempt | None] = relationship(back_populates="provider_events")

    __table_args__ = (
        CheckConstraint("length(payload_sha256) = 64", name="payload_sha256_length"),
        CheckConstraint("amount_cents > 0", name="amount_positive"),
        CheckConstraint("currency = 'CNY'", name="currency_cny"),
        CheckConstraint(
            "processing_status IN ('received', 'processed', 'ignored', 'rejected')",
            name="processing_status_allowed",
        ),
        CheckConstraint(
            "error_status_code IS NULL OR error_status_code BETWEEN 400 AND 599",
            name="error_status_code_range",
        ),
        CheckConstraint(
            "provider_state_raw <> 'SUCCESS' OR "
            "(provider_transaction_id IS NOT NULL AND provider_success_time IS NOT NULL)",
            name="success_has_transaction",
        ),
        UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_payment_provider_events_provider_event",
        ),
        Index("ix_payment_provider_events_attempt_received", "payment_attempt_id", "received_at"),
        Index(
            "ix_payment_provider_events_merchant_order",
            "provider",
            "provider_merchant_id",
            "merchant_order_no",
        ),
        Index("ix_payment_provider_events_status_received", "processing_status", "received_at"),
    )
