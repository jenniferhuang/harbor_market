"""Add mock-first payment attempts, state history, and provider inbox.

Revision ID: 0003_add_payment_attempts
Revises: 0002_add_product_catalog
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_add_payment_attempts"
down_revision: str | None = "0002_add_product_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("order_reference", sa.String(length=64), nullable=False),
        sa.Column("merchant_order_no", sa.String(length=32), nullable=False),
        sa.Column(
            "provider",
            sa.String(length=32),
            server_default=sa.text("'wechat_pay'"),
            nullable=False,
        ),
        sa.Column("provider_mode", sa.String(length=16), nullable=False),
        sa.Column("provider_app_id", sa.String(length=32), nullable=False),
        sa.Column("provider_merchant_id", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'created'"),
            nullable=False,
        ),
        sa.Column("provider_state_raw", sa.String(length=32), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column(
            "currency",
            sa.String(length=3),
            server_default=sa.text("'CNY'"),
            nullable=False,
        ),
        sa.Column("description", sa.String(length=127), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("prepay_id", sa.String(length=128), nullable=True),
        sa.Column("prepay_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "client_parameters",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("provider_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=500), nullable=True),
        sa.Column("close_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "review_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("review_reason", sa.String(length=80), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('created', 'pending', 'succeeded', 'closed', 'failed')",
            name=op.f("ck_payment_attempts_status_allowed"),
        ),
        sa.CheckConstraint(
            "provider_mode IN ('mock', 'live')",
            name=op.f("ck_payment_attempts_provider_mode_allowed"),
        ),
        sa.CheckConstraint(
            "amount_cents > 0",
            name=op.f("ck_payment_attempts_amount_positive"),
        ),
        sa.CheckConstraint(
            "currency = 'CNY'",
            name=op.f("ck_payment_attempts_currency_cny"),
        ),
        sa.CheckConstraint(
            "length(trim(order_reference)) BETWEEN 6 AND 64",
            name=op.f("ck_payment_attempts_order_reference_length"),
        ),
        sa.CheckConstraint(
            "length(merchant_order_no) BETWEEN 6 AND 32",
            name=op.f("ck_payment_attempts_merchant_order_no_length"),
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name=op.f("ck_payment_attempts_request_hash_length"),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_payment_attempts_version_positive"),
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR (prepay_id IS NOT NULL AND prepay_expires_at IS NOT NULL)",
            name=op.f("ck_payment_attempts_pending_has_prepay"),
        ),
        sa.CheckConstraint(
            "status <> 'succeeded' OR "
            "(provider_transaction_id IS NOT NULL AND paid_at IS NOT NULL)",
            name=op.f("ck_payment_attempts_success_has_transaction"),
        ),
        sa.CheckConstraint(
            "status <> 'closed' OR closed_at IS NOT NULL",
            name=op.f("ck_payment_attempts_closed_has_timestamp"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR failed_at IS NOT NULL",
            name=op.f("ck_payment_attempts_failed_has_timestamp"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name=op.f("fk_payment_attempts_owner_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_attempts")),
        sa.UniqueConstraint("public_id", name=op.f("uq_payment_attempts_public_id")),
        sa.UniqueConstraint(
            "owner_user_id",
            "idempotency_key",
            name="uq_payment_attempts_owner_idempotency_key",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_merchant_id",
            "merchant_order_no",
            name="uq_payment_attempts_provider_merchant_order",
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_transaction_id",
            name="uq_payment_attempts_provider_transaction",
        ),
    )
    op.create_index(
        "ix_payment_attempts_owner_created",
        "payment_attempts",
        ["owner_user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payment_attempts_order_created",
        "payment_attempts",
        ["order_reference", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_payment_attempts_status_updated",
        "payment_attempts",
        ["status", "updated_at"],
        unique=False,
    )
    op.create_index(
        "uq_payment_attempts_one_active_order",
        "payment_attempts",
        ["order_reference"],
        unique=True,
        postgresql_where=sa.text("status IN ('created', 'pending')"),
        sqlite_where=sa.text("status IN ('created', 'pending')"),
    )
    op.create_table(
        "payment_mock_provider_records",
        sa.Column("merchant_order_no", sa.String(length=32), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("app_id", sa.String(length=32), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("prepay_id", sa.String(length=128), nullable=False),
        sa.Column(
            "prepay_generation",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("prepay_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "client_parameters",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column(
            "trade_state",
            sa.String(length=32),
            server_default=sa.text("'NOTPAY'"),
            nullable=False,
        ),
        sa.Column("transaction_id", sa.String(length=64), nullable=True),
        sa.Column("success_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(request_fingerprint) = 64",
            name=op.f("ck_payment_mock_provider_records_request_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "amount_cents > 0",
            name=op.f("ck_payment_mock_provider_records_amount_positive"),
        ),
        sa.CheckConstraint(
            "currency = 'CNY'",
            name=op.f("ck_payment_mock_provider_records_currency_cny"),
        ),
        sa.CheckConstraint(
            "prepay_generation > 0",
            name=op.f("ck_payment_mock_provider_records_prepay_generation_positive"),
        ),
        sa.CheckConstraint(
            "trade_state IN ('NOTPAY', 'SUCCESS', 'CLOSED', 'PAYERROR', 'REFUND')",
            name=op.f("ck_payment_mock_provider_records_trade_state_allowed"),
        ),
        sa.CheckConstraint(
            "trade_state NOT IN ('SUCCESS', 'REFUND') OR "
            "(transaction_id IS NOT NULL AND success_time IS NOT NULL)",
            name=op.f("ck_payment_mock_provider_records_success_has_transaction"),
        ),
        sa.PrimaryKeyConstraint(
            "merchant_order_no",
            name=op.f("pk_payment_mock_provider_records"),
        ),
        sa.UniqueConstraint(
            "prepay_id",
            name=op.f("uq_payment_mock_provider_records_prepay_id"),
        ),
        sa.UniqueConstraint(
            "transaction_id",
            name=op.f("uq_payment_mock_provider_records_transaction_id"),
        ),
    )
    op.create_index(
        "ix_payment_mock_provider_state_updated",
        "payment_mock_provider_records",
        ["trade_state", "updated_at"],
        unique=False,
    )
    op.create_table(
        "payment_state_events",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("payment_attempt_id", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=True),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("details", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('api', 'provider_notification', 'provider_query', 'system')",
            name=op.f("ck_payment_state_events_source_allowed"),
        ),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN "
            "('created', 'pending', 'succeeded', 'closed', 'failed')",
            name=op.f("ck_payment_state_events_from_status_allowed"),
        ),
        sa.CheckConstraint(
            "to_status IS NULL OR to_status IN "
            "('created', 'pending', 'succeeded', 'closed', 'failed')",
            name=op.f("ck_payment_state_events_to_status_allowed"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"],
            ["payment_attempts.id"],
            name=op.f("fk_payment_state_events_payment_attempt_id_payment_attempts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_state_events")),
        sa.UniqueConstraint("event_id", name=op.f("uq_payment_state_events_event_id")),
    )
    op.create_index(
        "ix_payment_state_events_attempt_created",
        "payment_state_events",
        ["payment_attempt_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "payment_provider_events",
        sa.Column("id", sa.Integer(), sa.Identity(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("provider_app_id", sa.String(length=32), nullable=False),
        sa.Column("provider_merchant_id", sa.String(length=64), nullable=False),
        sa.Column("merchant_order_no", sa.String(length=32), nullable=False),
        sa.Column("provider_state_raw", sa.String(length=32), nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=64), nullable=True),
        sa.Column("provider_success_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("payment_attempt_id", sa.Integer(), nullable=True),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "signature_verified",
            sa.Boolean(),
            nullable=False,
        ),
        sa.Column(
            "processing_status",
            sa.String(length=20),
            server_default=sa.text("'received'"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_status_code", sa.Integer(), nullable=True),
        sa.Column("provider_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(payload_sha256) = 64",
            name=op.f("ck_payment_provider_events_payload_sha256_length"),
        ),
        sa.CheckConstraint(
            "amount_cents > 0",
            name=op.f("ck_payment_provider_events_amount_positive"),
        ),
        sa.CheckConstraint(
            "currency = 'CNY'",
            name=op.f("ck_payment_provider_events_currency_cny"),
        ),
        sa.CheckConstraint(
            "processing_status IN ('received', 'processed', 'ignored', 'rejected')",
            name=op.f("ck_payment_provider_events_processing_status_allowed"),
        ),
        sa.CheckConstraint(
            "error_status_code IS NULL OR error_status_code BETWEEN 400 AND 599",
            name=op.f("ck_payment_provider_events_error_status_code_range"),
        ),
        sa.CheckConstraint(
            "provider_state_raw <> 'SUCCESS' OR "
            "(provider_transaction_id IS NOT NULL AND provider_success_time IS NOT NULL)",
            name=op.f("ck_payment_provider_events_success_has_transaction"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_attempt_id"],
            ["payment_attempts.id"],
            name=op.f("fk_payment_provider_events_payment_attempt_id_payment_attempts"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_provider_events")),
        sa.UniqueConstraint(
            "provider",
            "provider_event_id",
            name="uq_payment_provider_events_provider_event",
        ),
    )
    op.create_index(
        "ix_payment_provider_events_attempt_received",
        "payment_provider_events",
        ["payment_attempt_id", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_payment_provider_events_status_received",
        "payment_provider_events",
        ["processing_status", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_payment_provider_events_merchant_order",
        "payment_provider_events",
        ["provider", "provider_merchant_id", "merchant_order_no"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_payment_provider_events_merchant_order",
        table_name="payment_provider_events",
    )
    op.drop_index(
        "ix_payment_provider_events_status_received",
        table_name="payment_provider_events",
    )
    op.drop_index(
        "ix_payment_provider_events_attempt_received",
        table_name="payment_provider_events",
    )
    op.drop_table("payment_provider_events")
    op.drop_index(
        "ix_payment_state_events_attempt_created",
        table_name="payment_state_events",
    )
    op.drop_table("payment_state_events")
    op.drop_index(
        "ix_payment_mock_provider_state_updated",
        table_name="payment_mock_provider_records",
    )
    op.drop_table("payment_mock_provider_records")
    op.drop_index("uq_payment_attempts_one_active_order", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_status_updated", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_order_created", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_owner_created", table_name="payment_attempts")
    op.drop_table("payment_attempts")
