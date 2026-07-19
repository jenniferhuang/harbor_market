from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.models import ImportJob, ObjectCleanupJob, User
from app.services.object_cleanup import (
    enqueue_object_cleanup,
    recover_stale_import_jobs,
    retryable_cleanup_jobs,
    run_object_cleanup_jobs,
)


def _image_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (3, 2), color=(31, 92, 77)).save(output, format="PNG")
    return output.getvalue()


def test_retry_claims_only_due_and_stale_cleanup_jobs(app: FastAPI) -> None:
    now = datetime.now(UTC)
    with app.state.session_factory() as session:
        future = enqueue_object_cleanup(
            session,
            ["products/staged/FUTURE/00000000000000000000000000000001.png"],
            reason="staging_expiry",
            not_before=now + timedelta(days=1),
        )[0]
        fresh_intent = enqueue_object_cleanup(
            session,
            ["products/catalog/FRESH/gallery/00000000000000000000000000000002.png"],
            reason="import_promotion_intent",
            status="intent",
        )[0]
        stale_intent = enqueue_object_cleanup(
            session,
            ["products/catalog/STALE/gallery/00000000000000000000000000000003.png"],
            reason="import_promotion_intent",
            status="intent",
        )[0]
        stale_intent.created_at = now - timedelta(minutes=11)
        stale_processing = enqueue_object_cleanup(
            session,
            ["products/catalog/PROCESS/gallery/00000000000000000000000000000004.png"],
            reason="import_promotion_intent",
            status="processing",
        )[0]
        stale_processing.updated_at = now - timedelta(minutes=11)
        due = enqueue_object_cleanup(
            session,
            ["products/staged/DUE/00000000000000000000000000000005.png"],
            reason="staging_expiry",
            not_before=now - timedelta(seconds=1),
        )[0]
        session.commit()

        claimed = retryable_cleanup_jobs(session)
        assert {job.id for job in claimed} == {
            stale_intent.id,
            stale_processing.id,
            due.id,
        }
        assert {job.status for job in claimed} == {"processing"}
        assert {job.not_before for job in claimed} == {None}
        assert session.get(ObjectCleanupJob, future.id).status == "pending"
        assert session.get(ObjectCleanupJob, fresh_intent.id).status == "intent"


def test_failed_cleanup_backoff_does_not_starve_the_next_job(
    app: FastAPI,
    fake_object_storage: Any,
) -> None:
    fake_object_storage.fail_delete = True
    with app.state.session_factory() as session:
        jobs = enqueue_object_cleanup(
            session,
            [f"products/cleanup-fairness/{index:03d}.png" for index in range(101)],
            reason="fairness_test",
        )
        session.commit()

        first_batch = retryable_cleanup_jobs(session, limit=100)
        assert [job.id for job in first_batch] == [job.id for job in jobs[:100]]
        failed = run_object_cleanup_jobs(session, fake_object_storage, first_batch)
        assert len(failed) == 100
        assert all(job.status == "failed" for job in failed)
        assert all(job.not_before is not None for job in failed)
        assert all(job.not_before > datetime.now(UTC) for job in failed)

        # Even after the failed jobs become due, an unattempted job must not sit
        # behind the same oldest retry batch forever.
        retry_due_at = datetime.now(UTC)
        for job in failed:
            job.not_before = retry_due_at
        session.commit()

        second_batch = retryable_cleanup_jobs(session, limit=100)
        assert second_batch[0].id == jobs[100].id
        assert jobs[100].id in {job.id for job in second_batch}


def test_due_failed_cleanup_is_not_starved_by_sustained_new_arrivals(
    app: FastAPI,
) -> None:
    now = datetime.now(UTC)
    with app.state.session_factory() as session:
        due_retry = enqueue_object_cleanup(
            session,
            ["products/cleanup-fairness/due-retry.png"],
            reason="fairness_test",
        )[0]
        due_retry.status = "failed"
        due_retry.attempts = 1
        due_retry.created_at = now - timedelta(hours=1)
        due_retry.not_before = now - timedelta(minutes=1)
        session.commit()

        new_jobs = enqueue_object_cleanup(
            session,
            [f"products/cleanup-fairness/new-{index:03d}.png" for index in range(100)],
            reason="fairness_test",
        )
        session.commit()

        claimed = retryable_cleanup_jobs(session, limit=100)
        assert claimed[0].id == due_retry.id
        assert due_retry.id in {job.id for job in claimed}
        assert new_jobs[-1].id not in {job.id for job in claimed}


def test_forced_retry_clears_backoff_so_stale_processing_can_recover(
    app: FastAPI,
) -> None:
    now = datetime.now(UTC)
    with app.state.session_factory() as session:
        failed = enqueue_object_cleanup(
            session,
            ["products/cleanup-fairness/forced-retry.png"],
            reason="forced_retry_test",
        )[0]
        failed.status = "failed"
        failed.attempts = 1
        failed.not_before = now + timedelta(days=1)
        session.commit()

        forced = retryable_cleanup_jobs(
            session,
            job_id=failed.id,
            limit=1,
            force_failed=True,
        )
        assert [job.id for job in forced] == [failed.id]
        assert forced[0].status == "processing"
        assert forced[0].not_before is None

        forced[0].updated_at = now - timedelta(minutes=11)
        session.commit()

        recovered = retryable_cleanup_jobs(session, job_id=failed.id, limit=1)
        assert [job.id for job in recovered] == [failed.id]
        assert recovered[0].status == "processing"
        assert recovered[0].not_before is None


def test_cleanup_never_deletes_an_object_with_a_live_product_image_reference(
    admin_client: TestClient,
    app: FastAPI,
    fake_object_storage: Any,
) -> None:
    category = admin_client.post(
        "/api/v1/admin/categories",
        json={"code": "CLEANUP", "name": "清理保护"},
    ).json()["data"]
    product = admin_client.post(
        "/api/v1/admin/products",
        json={
            "product_code": "CLEANUP-REF",
            "name": "清理引用保护",
            "category_id": category["id"],
            "base_price_cents": 100,
        },
    ).json()["data"]
    uploaded = admin_client.post(
        f"/api/v1/admin/products/{product['id']}/images",
        files={"file": ("image.png", _image_bytes(), "image/png")},
        data={"image_type": "gallery", "sort_order": "0"},
    )
    assert uploaded.status_code == 200, uploaded.text
    object_key = uploaded.json()["data"]["images"][0]["object_key"]

    with app.state.session_factory() as session:
        job = enqueue_object_cleanup(
            session,
            [object_key],
            reason="stale_intent_test",
        )[0]
        session.commit()
        failed = run_object_cleanup_jobs(session, fake_object_storage, [job])
        refreshed = session.get(ObjectCleanupJob, job.id)
        assert failed == []
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.attempts == 1

    assert object_key in fake_object_storage.objects
    assert object_key not in fake_object_storage.delete_calls


def test_cleanup_worker_expires_due_staging_and_recovers_stale_import(
    admin_client: TestClient,
    app: FastAPI,
    fake_object_storage: Any,
) -> None:
    key = "products/staged/EXPIRED/00000000000000000000000000000006.png"
    fake_object_storage.seed(key, _image_bytes(), content_type="image/png")
    with app.state.session_factory() as session:
        admin = session.scalar(select(User).where(User.username == "catalog-admin"))
        assert admin is not None
        cleanup = enqueue_object_cleanup(
            session,
            [key],
            reason="staging_expiry",
            not_before=datetime.now(UTC) - timedelta(seconds=1),
            created_by=admin.id,
        )[0]
        stale_import = ImportJob(
            creator=admin,
            original_filename="interrupted.xlsx",
            workbook_sha256="0" * 64,
            idempotency_key="stale-import-key",
            dry_run=False,
            status="pending",
            created_at=datetime.now(UTC) - timedelta(hours=4),
        )
        session.add(stale_import)
        session.commit()

        recovered = recover_stale_import_jobs(session)
        assert [job.id for job in recovered] == [stale_import.id]
        assert recovered[0].status == "failed"
        assert recovered[0].summary["recovered_stale"] == 1

        claimed = retryable_cleanup_jobs(session)
        assert [job.id for job in claimed] == [cleanup.id]
        assert run_object_cleanup_jobs(session, fake_object_storage, claimed) == []

    assert key not in fake_object_storage.objects
