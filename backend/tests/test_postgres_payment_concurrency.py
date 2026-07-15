from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.api.dependencies import get_current_admin
from app.core.config import Settings
from app.main import create_app
from app.models import (
    MockPaymentProviderRecord,
    PaymentAttempt,
    PaymentProviderEvent,
    PaymentStateEvent,
    User,
)
from app.payments.domain import ProviderTradeState
from app.payments.providers.mock_wechat import MockWeChatPayGateway
from app.services.payments import PaymentService

pytestmark = pytest.mark.postgres

_NOTIFY_PATH = "/api/v1/payments/providers/wechat-pay/notify"


@pytest.fixture(scope="module")
def postgres_database() -> Iterator[tuple[Engine, str]]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = make_url(database_url).database or ""
    if "test" not in database_name.casefold():
        pytest.skip("TEST_DATABASE_URL database name must contain 'test'")

    backend_dir = Path(__file__).resolve().parents[1]
    environment = {
        "DATABASE_URL": database_url,
        "PATH": "/usr/bin:/bin",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=environment,
        check=True,
    )
    engine = sa.create_engine(database_url, pool_pre_ping=True)
    try:
        yield engine, database_url
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def clean_postgres_payments(postgres_database: tuple[Engine, str]) -> Iterator[None]:
    engine, _database_url = postgres_database
    _truncate_application_tables(engine)
    try:
        yield
    finally:
        _truncate_application_tables(engine)


@pytest.fixture
def payment_app(postgres_database: tuple[Engine, str]) -> tuple[FastAPI, Engine]:
    engine, database_url = postgres_database
    admin_id = _seed_admin(engine)
    return _test_app(engine, database_url, admin_id), engine


def test_concurrent_same_idempotency_key_creates_one_attempt(
    payment_app: tuple[FastAPI, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = payment_app
    order_reference = "ORDER-PG-SAME-KEY-001"
    _synchronize_empty_order_checks(monkeypatch, order_reference)

    responses = _run_concurrently(
        app,
        [
            lambda client: _create_payment(
                client,
                order_reference=order_reference,
                idempotency_key="postgres-same-key-0001",
            ),
            lambda client: _create_payment(
                client,
                order_reference=order_reference,
                idempotency_key="postgres-same-key-0001",
            ),
        ],
    )

    assert [response.status_code for response in responses] == [201, 201]
    response_attempts = [response.json()["data"] for response in responses]
    assert len({attempt["public_id"] for attempt in response_attempts}) == 1
    assert len({attempt["merchant_order_no"] for attempt in response_attempts}) == 1
    with Session(engine) as session:
        attempts = list(
            session.scalars(
                sa.select(PaymentAttempt).where(PaymentAttempt.order_reference == order_reference)
            )
        )
        assert len(attempts) == 1
        assert attempts[0].status == "pending"
        provider_record_count = session.scalar(
            sa.select(sa.func.count()).select_from(MockPaymentProviderRecord)
        )
        assert provider_record_count == 1
        assert _state_event_count(session, attempts[0].id, "payment.attempt_created") == 1
        assert _state_event_count(session, attempts[0].id, "payment.prepay_created") == 1


def test_concurrent_different_keys_for_same_order_allow_one_active_attempt(
    payment_app: tuple[FastAPI, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = payment_app
    order_reference = "ORDER-PG-DIFFERENT-KEYS-001"
    _synchronize_empty_order_checks(monkeypatch, order_reference)

    responses = _run_concurrently(
        app,
        [
            lambda client: _create_payment(
                client,
                order_reference=order_reference,
                idempotency_key="postgres-different-key-0001",
            ),
            lambda client: _create_payment(
                client,
                order_reference=order_reference,
                idempotency_key="postgres-different-key-0002",
            ),
        ],
    )

    assert sorted(response.status_code for response in responses) == [201, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "payment_attempt_conflict"
    with Session(engine) as session:
        attempts = list(
            session.scalars(
                sa.select(PaymentAttempt).where(PaymentAttempt.order_reference == order_reference)
            )
        )
        assert len(attempts) == 1
        assert attempts[0].status == "pending"
        provider_record_count = session.scalar(
            sa.select(sa.func.count()).select_from(MockPaymentProviderRecord)
        )
        assert provider_record_count == 1


def test_concurrent_exact_callback_delivery_is_processed_once(
    payment_app: tuple[FastAPI, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = payment_app
    with TestClient(app) as client:
        payment = _payment_data(
            _create_payment(
                client,
                order_reference="ORDER-PG-CALLBACK-001",
                idempotency_key="postgres-callback-0001",
            )
        )
    gateway = _mock_gateway(app)
    gateway.set_trade_state(payment["merchant_order_no"], ProviderTradeState.SUCCESS)
    body, headers = gateway.build_success_notification(
        payment["merchant_order_no"],
        provider_event_id="postgres-exact-callback-0001",
    )
    _synchronize_notification_prechecks(monkeypatch)

    responses = _run_concurrently(
        app,
        [
            lambda client: client.post(_NOTIFY_PATH, content=body, headers=headers),
            lambda client: client.post(_NOTIFY_PATH, content=body, headers=headers),
        ],
    )

    assert [response.status_code for response in responses] == [204, 204]
    with Session(engine) as session:
        attempt = session.scalar(
            sa.select(PaymentAttempt).where(PaymentAttempt.public_id == payment["public_id"])
        )
        assert attempt is not None
        assert attempt.status == "succeeded"
        assert attempt.provider_transaction_id is not None
        receipt_count = session.scalar(
            sa.select(sa.func.count())
            .select_from(PaymentProviderEvent)
            .where(PaymentProviderEvent.provider_event_id == "postgres-exact-callback-0001")
        )
        assert receipt_count == 1
        assert _state_event_count(session, attempt.id, "payment.succeeded") == 1


def test_concurrent_late_successes_for_closed_siblings_flag_both_for_review(
    payment_app: tuple[FastAPI, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = payment_app
    order_reference = "ORDER-PG-LATE-SIBLINGS-001"
    with TestClient(app) as client:
        first = _payment_data(
            _create_payment(
                client,
                order_reference=order_reference,
                idempotency_key="postgres-late-sibling-0001",
            )
        )
        first_close = client.post(f"/api/v1/admin/payments/{first['public_id']}/close")
        assert first_close.status_code == 200
        assert first_close.json()["data"]["status"] == "closed"

        second = _payment_data(
            _create_payment(
                client,
                order_reference=order_reference,
                idempotency_key="postgres-late-sibling-0002",
            )
        )
        second_close = client.post(f"/api/v1/admin/payments/{second['public_id']}/close")
        assert second_close.status_code == 200
        assert second_close.json()["data"]["status"] == "closed"

    gateway = _mock_gateway(app)
    gateway.set_trade_state(first["merchant_order_no"], ProviderTradeState.SUCCESS)
    gateway.set_trade_state(second["merchant_order_no"], ProviderTradeState.SUCCESS)
    first_body, first_headers = gateway.build_success_notification(
        first["merchant_order_no"],
        provider_event_id="postgres-late-sibling-event-0001",
    )
    second_body, second_headers = gateway.build_success_notification(
        second["merchant_order_no"],
        provider_event_id="postgres-late-sibling-event-0002",
    )
    _synchronize_notification_prechecks(monkeypatch)

    responses = _run_concurrently(
        app,
        [
            lambda client: client.post(
                _NOTIFY_PATH,
                content=first_body,
                headers=first_headers,
            ),
            lambda client: client.post(
                _NOTIFY_PATH,
                content=second_body,
                headers=second_headers,
            ),
        ],
    )

    assert [response.status_code for response in responses] == [204, 204]
    with Session(engine) as session:
        attempts = list(
            session.scalars(
                sa.select(PaymentAttempt)
                .where(PaymentAttempt.order_reference == order_reference)
                .order_by(PaymentAttempt.id)
            )
        )
        assert len(attempts) == 2
        assert {attempt.status for attempt in attempts} == {"succeeded"}
        assert all(attempt.review_required for attempt in attempts)
        assert {attempt.review_reason for attempt in attempts} == {"multiple_successful_attempts"}
        assert len({attempt.provider_transaction_id for attempt in attempts}) == 2
        assert (
            sum(
                _state_event_count(session, attempt.id, "payment.succeeded_late")
                for attempt in attempts
            )
            == 2
        )
        assert (
            sum(
                _state_event_count(session, attempt.id, "payment.review_required")
                for attempt in attempts
            )
            == 2
        )


def test_concurrent_identical_provider_transaction_ids_pay_only_one_order(
    payment_app: tuple[FastAPI, Engine],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, engine = payment_app
    with TestClient(app) as client:
        first = _payment_data(
            _create_payment(
                client,
                order_reference="ORDER-PG-TRANSACTION-A-001",
                idempotency_key="postgres-transaction-a-0001",
            )
        )
        second = _payment_data(
            _create_payment(
                client,
                order_reference="ORDER-PG-TRANSACTION-B-001",
                idempotency_key="postgres-transaction-b-0001",
            )
        )

    gateway = _mock_gateway(app)
    gateway.set_trade_state(first["merchant_order_no"], ProviderTradeState.SUCCESS)
    gateway.set_trade_state(second["merchant_order_no"], ProviderTradeState.SUCCESS)
    shared_transaction_id = "mock_tx_postgres_shared_transaction"
    first_body, first_headers = gateway.build_success_notification(
        first["merchant_order_no"],
        provider_event_id="postgres-shared-transaction-event-a",
        transaction_id=shared_transaction_id,
    )
    second_body, second_headers = gateway.build_success_notification(
        second["merchant_order_no"],
        provider_event_id="postgres-shared-transaction-event-b",
        transaction_id=shared_transaction_id,
    )
    _synchronize_success_application(monkeypatch)

    responses = _run_concurrently(
        app,
        [
            lambda client: client.post(
                _NOTIFY_PATH,
                content=first_body,
                headers=first_headers,
            ),
            lambda client: client.post(
                _NOTIFY_PATH,
                content=second_body,
                headers=second_headers,
            ),
        ],
    )

    assert sorted(response.status_code for response in responses) == [204, 409]
    rejected = next(response for response in responses if response.status_code == 409)
    assert rejected.json()["error"]["code"] == "provider_transaction_conflict"
    with Session(engine) as session:
        attempts = list(
            session.scalars(
                sa.select(PaymentAttempt).where(
                    PaymentAttempt.order_reference.in_(
                        ["ORDER-PG-TRANSACTION-A-001", "ORDER-PG-TRANSACTION-B-001"]
                    )
                )
            )
        )
        assert len(attempts) == 2
        assert sorted(attempt.status for attempt in attempts) == ["pending", "succeeded"]
        assert (
            sum(attempt.provider_transaction_id == shared_transaction_id for attempt in attempts)
            == 1
        )
        assert (
            sum(
                _state_event_count(session, attempt.id, "payment.succeeded") for attempt in attempts
            )
            == 1
        )
        receipts = list(
            session.scalars(
                sa.select(PaymentProviderEvent).where(
                    PaymentProviderEvent.provider_event_id.in_(
                        [
                            "postgres-shared-transaction-event-a",
                            "postgres-shared-transaction-event-b",
                        ]
                    )
                )
            )
        )
        assert len(receipts) == 2
        assert sum(receipt.processing_status == "processed" for receipt in receipts) == 1
        assert sum(receipt.processing_status == "rejected" for receipt in receipts) == 1
        rejected_receipt = next(
            receipt for receipt in receipts if receipt.processing_status == "rejected"
        )
        assert rejected_receipt.error_code == "provider_transaction_conflict"
        assert rejected_receipt.error_status_code == 409


def _truncate_application_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "TRUNCATE TABLE payment_provider_events, payment_state_events, "
                "payment_mock_provider_records, payment_attempts, object_cleanup_jobs, "
                "import_jobs, product_images, product_skus, products, categories, users "
                "RESTART IDENTITY CASCADE"
            )
        )


def _seed_admin(engine: Engine) -> int:
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        admin = User(
            username="postgres-payment-concurrency-admin",
            password_hash="$argon2id$postgres-payment-concurrency",
            is_admin=True,
        )
        session.add(admin)
        session.commit()
        return admin.id


def _test_app(engine: Engine, database_url: str, admin_id: int) -> FastAPI:
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=database_url,
        auth_secret_key="postgres-payment-concurrency-signing-key",
        auth_cookie_secure=False,
        allowed_hosts="testserver",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8_192,
        argon2_parallelism=1,
        payment_mode="mock",
        payment_mock_controls_enabled=True,
        payment_mock_signing_secret="postgres-mock-payment-signing-secret-32-bytes",
    )
    app = create_app(settings, engine=engine)
    admin = SimpleNamespace(id=admin_id, is_admin=True)

    def override_admin() -> SimpleNamespace:
        return admin

    app.dependency_overrides[get_current_admin] = override_admin
    return app


def _create_payment(
    client: TestClient,
    *,
    order_reference: str,
    idempotency_key: str,
) -> Response:
    return client.post(
        "/api/v1/admin/payments",
        headers={"X-Idempotency-Key": idempotency_key},
        json={
            "order_reference": order_reference,
            "amount_cents": 1_990,
            "currency": "CNY",
            "description": "PostgreSQL payment concurrency checkout",
        },
    )


def _payment_data(response: Response) -> dict[str, Any]:
    assert response.status_code == 201, response.text
    return response.json()["data"]


def _mock_gateway(app: FastAPI) -> MockWeChatPayGateway:
    gateway = app.state.payment_gateway
    assert isinstance(gateway, MockWeChatPayGateway)
    return gateway


def _run_concurrently(
    app: FastAPI,
    operations: list[Callable[[TestClient], Response]],
) -> list[Response]:
    start = Barrier(len(operations))

    def run(client: TestClient, operation: Callable[[TestClient], Response]) -> Response:
        start.wait(timeout=10)
        return operation(client)

    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app)) for _operation in operations]
        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            futures = [
                executor.submit(run, client, operation)
                for client, operation in zip(clients, operations, strict=True)
            ]
            return [future.result(timeout=20) for future in futures]


def _synchronize_empty_order_checks(
    monkeypatch: pytest.MonkeyPatch,
    target_order_reference: str,
) -> None:
    original = PaymentService._lock_order_attempts
    arrivals = 0
    guard = Lock()
    arrival_barrier = Barrier(2)

    def synchronized(self: PaymentService, order_reference: str) -> list[PaymentAttempt]:
        nonlocal arrivals
        attempts = original(self, order_reference)
        should_wait = False
        with guard:
            if order_reference == target_order_reference and not attempts and arrivals < 2:
                arrivals += 1
                should_wait = True
        if should_wait:
            arrival_barrier.wait(timeout=10)
        return attempts

    monkeypatch.setattr(PaymentService, "_lock_order_attempts", synchronized)


def _synchronize_notification_prechecks(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PaymentService._lock_attempt_by_merchant_order
    arrival_barrier = Barrier(2)

    def synchronized(
        self: PaymentService,
        *,
        provider: str,
        provider_merchant_id: str,
        merchant_order_no: str,
    ) -> PaymentAttempt | None:
        arrival_barrier.wait(timeout=10)
        return original(
            self,
            provider=provider,
            provider_merchant_id=provider_merchant_id,
            merchant_order_no=merchant_order_no,
        )

    monkeypatch.setattr(PaymentService, "_lock_attempt_by_merchant_order", synchronized)


def _synchronize_success_application(monkeypatch: pytest.MonkeyPatch) -> None:
    original = PaymentService._apply_snapshot
    arrival_barrier = Barrier(2)
    arrivals = 0
    guard = Lock()

    def synchronized(
        self: PaymentService,
        attempt: PaymentAttempt,
        snapshot: Any,
        *,
        source: str,
        reason_override: str | None = None,
    ) -> Any:
        nonlocal arrivals
        if source == "provider_notification" and snapshot.trade_state == ProviderTradeState.SUCCESS:
            should_wait = False
            with guard:
                if arrivals < 2:
                    arrivals += 1
                    should_wait = True
            if should_wait:
                arrival_barrier.wait(timeout=10)
        return original(
            self,
            attempt,
            snapshot,
            source=source,
            reason_override=reason_override,
        )

    monkeypatch.setattr(PaymentService, "_apply_snapshot", synchronized)


def _state_event_count(session: Session, attempt_id: int, event_type: str) -> int:
    return (
        session.scalar(
            sa.select(sa.func.count())
            .select_from(PaymentStateEvent)
            .where(
                PaymentStateEvent.payment_attempt_id == attempt_id,
                PaymentStateEvent.event_type == event_type,
            )
        )
        or 0
    )
