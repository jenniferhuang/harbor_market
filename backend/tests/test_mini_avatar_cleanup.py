from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select

from app.models import MiniCustomer, ObjectCleanupJob
from app.services.mini_avatar import prepare_avatar, replace_avatar
from app.services.object_cleanup import (
    enqueue_object_cleanup,
    retryable_cleanup_jobs,
    run_object_cleanup_jobs,
)


def _avatar() -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 400), "green").save(output, format="PNG")
    return prepare_avatar(output.getvalue())


def _customer(session: Any) -> MiniCustomer:
    customer = MiniCustomer(app_id="wx-test", openid="openid-cleanup")
    session.add(customer)
    session.commit()
    return customer


def test_cleanup_never_deletes_current_customer_avatar(
    app: FastAPI, fake_object_storage: Any
) -> None:
    with app.state.session_factory() as session:
        customer = replace_avatar(session, fake_object_storage, _customer(session), _avatar())
        key = customer.avatar_object_key
        assert key is not None
        intent = enqueue_object_cleanup(
            session, [key], reason="mini_avatar_upload_intent", status="intent"
        )[0]
        intent.created_at = datetime.now(UTC) - timedelta(minutes=11)
        session.commit()
        claimed = retryable_cleanup_jobs(session)
        assert [job.id for job in claimed] == [intent.id]
        assert run_object_cleanup_jobs(session, fake_object_storage, claimed) == []
        assert key in fake_object_storage.objects
        assert key not in fake_object_storage.delete_calls
        assert intent.status == "completed"


def test_failed_replacement_cleanup_is_durable_and_retryable(
    app: FastAPI, fake_object_storage: Any
) -> None:
    with app.state.session_factory() as session:
        customer = replace_avatar(session, fake_object_storage, _customer(session), _avatar())
        old_key = customer.avatar_object_key
        fake_object_storage.fail_delete = True
        customer = replace_avatar(session, fake_object_storage, customer, _avatar())
        new_key = customer.avatar_object_key
        assert new_key != old_key
        assert old_key in fake_object_storage.objects
        assert new_key in fake_object_storage.objects
        job = session.scalar(
            select(ObjectCleanupJob).where(ObjectCleanupJob.reason == "mini_avatar_replaced")
        )
        assert job is not None and job.status == "failed" and job.not_before is not None
        fake_object_storage.fail_delete = False
        claimed = retryable_cleanup_jobs(session, job_id=job.id, force_failed=True)
        assert run_object_cleanup_jobs(session, fake_object_storage, claimed) == []
        assert old_key not in fake_object_storage.objects
        assert new_key in fake_object_storage.objects


def test_crashed_upload_intent_reclaims_unreferenced_avatar(
    app: FastAPI, fake_object_storage: Any
) -> None:
    key = "customers/1/avatars/unattached.jpg"
    fake_object_storage.put(key, _avatar(), content_type="image/jpeg")
    with app.state.session_factory() as session:
        intent = enqueue_object_cleanup(
            session, [key], reason="mini_avatar_upload_intent", status="intent"
        )[0]
        intent.created_at = datetime.now(UTC) - timedelta(minutes=11)
        session.commit()
        claimed = retryable_cleanup_jobs(session)
        assert run_object_cleanup_jobs(session, fake_object_storage, claimed) == []
        assert key not in fake_object_storage.objects
