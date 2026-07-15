from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.config import Settings
from app.models import (
    MockPaymentProviderRecord,
    PaymentAttempt,
    PaymentProviderEvent,
    PaymentStateEvent,
)
from app.payments.domain import ProviderTradeState
from app.payments.providers.mock_wechat import MockWeChatPayGateway


def _create_payment(
    client: TestClient,
    *,
    order_reference: str = "ORDER-20260715-001",
    amount_cents: int = 1_990,
    idempotency_key: str = "payment-idem-0001",
) -> Any:
    return client.post(
        "/api/v1/admin/payments",
        headers={"X-Idempotency-Key": idempotency_key},
        json={
            "order_reference": order_reference,
            "amount_cents": amount_cents,
            "currency": "CNY",
            "description": "Harbor Market mock checkout",
        },
    )


def _payment_data(response: Any) -> dict[str, Any]:
    assert response.status_code in {200, 201}, response.text
    return response.json()["data"]


def _mock_gateway(app: FastAPI) -> MockWeChatPayGateway:
    gateway = app.state.payment_gateway
    assert isinstance(gateway, MockWeChatPayGateway)
    return gateway


def test_mock_payment_configuration_is_impossible_in_production() -> None:
    with pytest.raises(ValidationError, match="mock payments cannot be enabled in production"):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://user:password@db/test",
            auth_secret_key="production-auth-secret-that-is-long-enough",
            payment_mode="mock",
            payment_mock_signing_secret="mock-signing-secret-that-is-long-enough",
        )


def test_mock_creation_requires_admin_auth_idempotency_and_same_origin(
    client: TestClient,
    admin_client: TestClient,
) -> None:
    admin_client.post("/api/v1/auth/logout")
    unauthenticated = _create_payment(admin_client)
    assert unauthenticated.status_code == 401

    credentials = {"username": "payment-user", "password": "correct horse battery staple"}
    assert client.post("/api/v1/auth/register", json=credentials).status_code == 201
    assert client.post("/api/v1/auth/login", json=credentials).status_code == 200
    non_admin = _create_payment(client)
    assert non_admin.status_code == 403

    assert client.post("/api/v1/auth/logout").status_code == 200
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "catalog-admin", "password": "correct horse battery staple"},
        ).status_code
        == 200
    )
    missing_key = client.post(
        "/api/v1/admin/payments",
        json={
            "order_reference": "ORDER-NO-KEY",
            "amount_cents": 100,
            "currency": "CNY",
            "description": "No idempotency key",
        },
    )
    assert missing_key.status_code == 422
    assert missing_key.json()["error"]["code"] == "idempotency_key_required"

    hostile = client.post(
        "/api/v1/admin/payments",
        headers={
            "Origin": "https://attacker.example",
            "X-Idempotency-Key": "hostile-idempotency",
        },
        json={
            "order_reference": "ORDER-HOSTILE",
            "amount_cents": 100,
            "currency": "CNY",
            "description": "Hostile request",
        },
    )
    assert hostile.status_code == 403
    assert hostile.json()["error"]["code"] == "csrf_origin_mismatch"


def test_create_is_pending_audited_and_strictly_idempotent(
    admin_client: TestClient,
) -> None:
    first = _payment_data(_create_payment(admin_client))
    assert first["status"] == "pending"
    assert first["provider_state_raw"] == "NOTPAY"
    assert first["amount_cents"] == 1_990
    assert first["currency"] == "CNY"
    assert first["merchant_order_no"].startswith("HM")
    assert len(first["merchant_order_no"]) == 32
    assert first["client_parameters"]["mock"] is True
    assert first["client_parameters"]["signType"] == "MOCK-HMAC-SHA256"

    replay = _payment_data(_create_payment(admin_client))
    assert replay["public_id"] == first["public_id"]
    assert replay["merchant_order_no"] == first["merchant_order_no"]

    detail = admin_client.get(f"/api/v1/admin/payments/{first['public_id']}")
    assert detail.status_code == 200
    assert [event["event_type"] for event in detail.json()["data"]["events"]] == [
        "payment.attempt_created",
        "payment.prepay_created",
    ]

    conflict = _create_payment(admin_client, amount_cents=2_000)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"


def test_active_attempt_blocks_a_new_key_and_closed_attempt_allows_retry(
    admin_client: TestClient,
) -> None:
    first = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-RETRY-001",
            idempotency_key="retry-idempotency-1",
        )
    )
    active_conflict = _create_payment(
        admin_client,
        order_reference="ORDER-RETRY-001",
        idempotency_key="retry-idempotency-2",
    )
    assert active_conflict.status_code == 409
    assert active_conflict.json()["error"]["code"] == "payment_attempt_in_progress"

    closed = admin_client.post(f"/api/v1/admin/payments/{first['public_id']}/close")
    assert closed.status_code == 200
    assert closed.json()["data"]["status"] == "closed"
    assert closed.json()["data"]["close_reason"] == "merchant_requested"

    second = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-RETRY-001",
            idempotency_key="retry-idempotency-2",
        )
    )
    assert second["status"] == "pending"
    assert second["public_id"] != first["public_id"]
    assert second["merchant_order_no"] != first["merchant_order_no"]


def test_callback_success_is_deduplicated_and_cannot_be_closed(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-CALLBACK-001",
            idempotency_key="callback-idempotency",
        )
    )
    simulated = admin_client.post(
        f"/api/v1/admin/payments/{payment['public_id']}/mock/provider-state",
        json={
            "trade_state": "SUCCESS",
            "deliver_callback": True,
            "provider_event_id": "callback-event-001",
        },
    )
    succeeded = _payment_data(simulated)
    assert succeeded["status"] == "succeeded"
    assert succeeded["provider_transaction_id"].startswith("mock_tx_")
    assert succeeded["paid_at"] is not None

    duplicate = admin_client.post(
        f"/api/v1/admin/payments/{payment['public_id']}/mock/provider-state",
        json={
            "trade_state": "SUCCESS",
            "deliver_callback": True,
            "provider_event_id": "callback-event-001",
        },
    )
    # A newly signed body with the same provider ID is correctly treated as a
    # conflict; exact callback replay is covered separately below.
    assert duplicate.status_code in {200, 409}

    with app.state.session_factory() as session:
        receipt_count = session.scalar(
            select(func.count())
            .select_from(PaymentProviderEvent)
            .where(PaymentProviderEvent.provider_event_id == "callback-event-001")
        )
        success_count = session.scalar(
            select(func.count())
            .select_from(PaymentStateEvent)
            .join(PaymentAttempt)
            .where(
                PaymentAttempt.public_id == payment["public_id"],
                PaymentStateEvent.event_type == "payment.succeeded",
            )
        )
    assert receipt_count == 1
    assert success_count == 1

    close = admin_client.post(f"/api/v1/admin/payments/{payment['public_id']}/close")
    assert close.status_code == 409
    assert close.json()["error"]["code"] == "payment_already_succeeded"


def test_dropped_callback_is_recovered_by_query_and_close_pay_race_favors_success(
    admin_client: TestClient,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-QUERY-001",
            idempotency_key="query-idempotency",
        )
    )
    provider_only = admin_client.post(
        f"/api/v1/admin/payments/{payment['public_id']}/mock/provider-state",
        json={"trade_state": "SUCCESS", "deliver_callback": False},
    )
    assert provider_only.status_code == 200
    assert provider_only.json()["data"]["status"] == "pending"

    # Close queries first. It observes the already-paid provider state and must
    # not overwrite it with CLOSED.
    close_race = admin_client.post(f"/api/v1/admin/payments/{payment['public_id']}/close")
    assert close_race.status_code == 200
    assert close_race.json()["data"]["status"] == "succeeded"


def test_signed_notification_rejects_tampering_amount_mismatch_and_event_id_reuse(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-SIGNATURE-001",
            idempotency_key="signature-idempotency",
        )
    )
    gateway = _mock_gateway(app)
    gateway.set_trade_state(payment["merchant_order_no"], ProviderTradeState.SUCCESS)
    body, headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="signed-event-001",
    )

    invalid_headers = dict(headers)
    invalid_headers["Wechatpay-Signature"] = "0" * 64
    invalid = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=body,
        headers=invalid_headers,
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "notification_signature_invalid"

    mismatched_body, mismatched_headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="amount-event-001",
        amount_cents=payment["amount_cents"] + 1,
    )
    mismatch = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=mismatched_body,
        headers=mismatched_headers,
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "provider_amount_mismatch"
    mismatch_replay = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=mismatched_body,
        headers=mismatched_headers,
    )
    assert mismatch_replay.status_code == 422
    assert mismatch_replay.json()["error"]["code"] == "provider_amount_mismatch"

    accepted = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=body,
        headers=headers,
    )
    assert accepted.status_code == 204
    exact_replay = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=body,
        headers=headers,
    )
    assert exact_replay.status_code == 204

    changed_body, changed_headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="signed-event-001",
        amount_cents=payment["amount_cents"] + 2,
    )
    reused = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=changed_body,
        headers=changed_headers,
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "provider_event_conflict"


def test_authoritative_late_success_corrects_closed_but_success_never_regresses(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-LATE-001",
            idempotency_key="late-idempotency",
        )
    )
    assert (
        admin_client.post(f"/api/v1/admin/payments/{payment['public_id']}/close").json()["data"][
            "status"
        ]
        == "closed"
    )

    gateway = _mock_gateway(app)
    gateway.set_trade_state(payment["merchant_order_no"], ProviderTradeState.SUCCESS)
    body, headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="late-success-event",
    )
    assert (
        admin_client.post(
            "/api/v1/payments/providers/wechat-pay/notify",
            content=body,
            headers=headers,
        ).status_code
        == 204
    )

    detail = admin_client.get(f"/api/v1/admin/payments/{payment['public_id']}").json()["data"]
    assert detail["status"] == "succeeded"
    assert "payment.succeeded_late" in {event["event_type"] for event in detail["events"]}

    gateway.set_trade_state(payment["merchant_order_no"], ProviderTradeState.CLOSED)
    reconciled = admin_client.post(f"/api/v1/admin/payments/{payment['public_id']}/reconcile")
    assert reconciled.status_code == 200
    assert reconciled.json()["data"]["status"] == "succeeded"


def test_success_prevents_another_attempt_for_the_same_order(
    admin_client: TestClient,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-PAID-001",
            idempotency_key="paid-idempotency-1",
        )
    )
    assert (
        admin_client.post(
            f"/api/v1/admin/payments/{payment['public_id']}/mock/provider-state",
            json={"trade_state": "SUCCESS", "deliver_callback": True},
        ).status_code
        == 200
    )

    retry = _create_payment(
        admin_client,
        order_reference="ORDER-PAID-001",
        idempotency_key="paid-idempotency-2",
    )
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "order_already_paid"


def test_success_identifiers_are_immutable(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-IMMUTABLE-001",
            idempotency_key="immutable-idempotency",
        )
    )
    gateway = _mock_gateway(app)
    gateway.set_trade_state(payment["merchant_order_no"], ProviderTradeState.SUCCESS)
    body, headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="immutable-event-1",
    )
    assert (
        admin_client.post(
            "/api/v1/payments/providers/wechat-pay/notify",
            content=body,
            headers=headers,
        ).status_code
        == 204
    )

    changed_body, changed_headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="immutable-event-2",
        transaction_id="mock_tx_changed_financial_identity",
    )
    changed = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=changed_body,
        headers=changed_headers,
    )
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "provider_transaction_mismatch"
    replay = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=changed_body,
        headers=changed_headers,
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "provider_transaction_mismatch"


def test_late_double_success_is_recorded_and_flagged_for_review(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    first = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-DOUBLE-001",
            idempotency_key="double-idempotency-1",
        )
    )
    assert (
        admin_client.post(f"/api/v1/admin/payments/{first['public_id']}/close").status_code == 200
    )
    second = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-DOUBLE-001",
            idempotency_key="double-idempotency-2",
        )
    )
    assert (
        admin_client.post(
            f"/api/v1/admin/payments/{second['public_id']}/mock/provider-state",
            json={"trade_state": "SUCCESS", "deliver_callback": True},
        ).status_code
        == 200
    )

    gateway = _mock_gateway(app)
    gateway.set_trade_state(first["merchant_order_no"], ProviderTradeState.SUCCESS)
    body, headers = gateway.build_success_notification(
        first["merchant_order_no"],
        provider_event_id="late-double-success-event",
    )
    assert (
        admin_client.post(
            "/api/v1/payments/providers/wechat-pay/notify",
            content=body,
            headers=headers,
        ).status_code
        == 204
    )

    first_detail = admin_client.get(f"/api/v1/admin/payments/{first['public_id']}").json()["data"]
    second_detail = admin_client.get(f"/api/v1/admin/payments/{second['public_id']}").json()["data"]
    assert first_detail["status"] == second_detail["status"] == "succeeded"
    assert first_detail["review_required"] is True
    assert second_detail["review_required"] is True
    assert first_detail["review_reason"] == "multiple_successful_attempts"
    assert second_detail["review_reason"] == "multiple_successful_attempts"
    first_event_types = [event["event_type"] for event in first_detail["events"]]
    assert first_event_types.index("payment.succeeded_late") < first_event_types.index(
        "payment.review_required"
    )


def test_mock_provider_state_survives_gateway_restart_and_refreshes_expired_prepay(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-DURABLE-001",
            idempotency_key="durable-idempotency",
        )
    )
    original_package = payment["client_parameters"]["package"]
    settings = app.state.settings
    assert settings.payment_mock_signing_secret is not None
    future = datetime.now(UTC) + timedelta(hours=3)
    restarted_gateway = MockWeChatPayGateway(
        app_id=settings.payment_mock_app_id,
        signing_secret=settings.payment_mock_signing_secret.get_secret_value(),
        session_factory=app.state.session_factory,
        prepay_ttl_seconds=settings.payment_prepay_ttl_seconds,
        now=lambda: future,
    )
    assert (
        restarted_gateway.query_payment(payment["merchant_order_no"]).trade_state
        == ProviderTradeState.NOTPAY
    )

    with app.state.session_factory() as session:
        attempt = session.scalar(
            select(PaymentAttempt).where(PaymentAttempt.public_id == payment["public_id"])
        )
        assert attempt is not None
        attempt.prepay_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    app.state.payment_gateway = restarted_gateway
    refreshed = admin_client.post(f"/api/v1/admin/payments/{payment['public_id']}/refresh-prepay")
    refreshed_data = _payment_data(refreshed)
    assert refreshed_data["status"] == "pending"
    assert refreshed_data["client_parameters"]["package"] != original_package

    with app.state.session_factory() as session:
        provider_record = session.get(
            MockPaymentProviderRecord,
            payment["merchant_order_no"],
        )
        assert provider_record is not None
        assert provider_record.prepay_generation == 2
    detail = admin_client.get(f"/api/v1/admin/payments/{payment['public_id']}").json()["data"]
    assert "payment.prepay_refreshed" in {event["event_type"] for event in detail["events"]}


def test_unknown_signed_callback_is_durably_normalized_and_exactly_deduplicated(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-UNKNOWN-CALLBACK",
            idempotency_key="unknown-callback-idempotency",
        )
    )
    gateway = _mock_gateway(app)
    gateway.set_trade_state(payment["merchant_order_no"], ProviderTradeState.SUCCESS)
    unknown_order_no = "HM" + "0" * 30
    body, headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="unknown-order-event",
        out_trade_no=unknown_order_no,
    )
    for _ in range(2):
        response = admin_client.post(
            "/api/v1/payments/providers/wechat-pay/notify",
            content=body,
            headers=headers,
        )
        assert response.status_code == 204

    with app.state.session_factory() as session:
        receipts = list(
            session.scalars(
                select(PaymentProviderEvent).where(
                    PaymentProviderEvent.provider_event_id == "unknown-order-event"
                )
            )
        )
        assert len(receipts) == 1
        receipt = receipts[0]
        assert receipt.processing_status == "ignored"
        assert receipt.error_code == "payment_not_found"
        assert receipt.payment_attempt_id is None
        assert receipt.provider_app_id == gateway.app_id
        assert receipt.provider_merchant_id == gateway.merchant_id
        assert receipt.merchant_order_no == unknown_order_no
        assert receipt.provider_state_raw == "SUCCESS"
        assert receipt.provider_transaction_id is not None
        assert receipt.provider_success_time is not None
        assert receipt.amount_cents == payment["amount_cents"]
        assert receipt.currency == "CNY"


def test_state_history_prevents_payment_attempt_deletion(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-AUDIT-RESTRICT",
            idempotency_key="audit-restrict-idempotency",
        )
    )
    with app.state.session_factory() as session:
        attempt = session.scalar(
            select(PaymentAttempt).where(PaymentAttempt.public_id == payment["public_id"])
        )
        assert attempt is not None
        session.delete(attempt)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_mock_rejects_non_jsapi_payerror_control_and_bounds_callback_body(
    admin_client: TestClient,
    app: FastAPI,
) -> None:
    payment = _payment_data(
        _create_payment(
            admin_client,
            order_reference="ORDER-MOCK-BOUNDARY",
            idempotency_key="mock-boundary-idempotency",
        )
    )
    unsupported = admin_client.post(
        f"/api/v1/admin/payments/{payment['public_id']}/mock/provider-state",
        json={"trade_state": "PAYERROR", "deliver_callback": False},
    )
    assert unsupported.status_code == 422

    oversized = admin_client.post(
        "/api/v1/payments/providers/wechat-pay/notify",
        content=b"x" * (app.state.settings.payment_webhook_max_bytes + 1),
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "payment_notification_too_large"
