from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import ImportJob, ObjectCleanupJob, ProductImage
from app.services.object_storage import ObjectStorage, ObjectStorageNotFoundError

logger = logging.getLogger(__name__)
_INTENT_GRACE = timedelta(minutes=10)
_PROCESSING_GRACE = timedelta(minutes=10)
_RETRY_BACKOFF_BASE = timedelta(minutes=1)
_RETRY_BACKOFF_MAX = timedelta(hours=1)
IMPORT_LEASE = timedelta(hours=3)


def enqueue_object_cleanup(
    session: Session,
    object_keys: list[str],
    *,
    reason: str,
    status: str = "pending",
    not_before: datetime | None = None,
    created_by: int | None = None,
) -> list[ObjectCleanupJob]:
    jobs = [
        ObjectCleanupJob(
            object_key=object_key,
            reason=reason,
            status=status,
            not_before=not_before,
            created_by=created_by,
        )
        for object_key in dict.fromkeys(object_keys)
    ]
    session.add_all(jobs)
    session.flush()
    return jobs


def run_object_cleanup_jobs(
    session: Session,
    storage: ObjectStorage,
    jobs: list[ObjectCleanupJob],
) -> list[ObjectCleanupJob]:
    failed: list[ObjectCleanupJob] = []
    for original in jobs:
        job = session.get(ObjectCleanupJob, original.id)
        if job is None or job.status == "completed":
            continue
        if job.status != "processing" or job.not_before is not None:
            job.status = "processing"
            job.not_before = None
            session.commit()
        job.attempts += 1
        # A cleanup intent can become stale while another transaction finishes
        # attaching the object. Never delete a key that is currently referenced.
        live_reference = session.scalar(
            select(ProductImage.id).where(ProductImage.object_key == job.object_key).limit(1)
        )
        if live_reference is not None:
            job.status = "completed"
            job.last_error = None
            job.not_before = None
            job.completed_at = datetime.now(UTC)
            session.commit()
            continue
        try:
            storage.delete(job.object_key)
        except ObjectStorageNotFoundError:
            job.status = "completed"
            job.last_error = None
            job.not_before = None
            job.completed_at = datetime.now(UTC)
        except Exception as exc:
            job.status = "failed"
            job.last_error = f"{type(exc).__name__}: {exc}"[:1_000]
            job.not_before = datetime.now(UTC) + _retry_backoff(job.attempts)
            failed.append(job)
            logger.warning(
                "Object cleanup job %s failed for %s",
                job.id,
                job.object_key,
            )
        else:
            job.status = "completed"
            job.last_error = None
            job.not_before = None
            job.completed_at = datetime.now(UTC)
        session.commit()
    return failed


def retryable_cleanup_jobs(
    session: Session,
    *,
    job_id: int | None = None,
    limit: int = 100,
    force_failed: bool = False,
) -> list[ObjectCleanupJob]:
    stale_intent_before = datetime.now(UTC) - _INTENT_GRACE
    stale_processing_before = datetime.now(UTC) - _PROCESSING_GRACE
    due_now = datetime.now(UTC)
    due_condition = or_(
        ObjectCleanupJob.not_before.is_(None),
        ObjectCleanupJob.not_before <= due_now,
    )
    if force_failed:
        if job_id is None:
            raise ValueError("force_failed requires a specific cleanup job")
        due_condition = or_(due_condition, ObjectCleanupJob.status == "failed")
    statement = (
        select(ObjectCleanupJob)
        .where(
            due_condition,
            or_(
                ObjectCleanupJob.status.in_(("pending", "failed")),
                and_(
                    ObjectCleanupJob.status == "intent",
                    ObjectCleanupJob.created_at <= stale_intent_before,
                ),
                and_(
                    ObjectCleanupJob.status == "processing",
                    ObjectCleanupJob.updated_at <= stale_processing_before,
                ),
            ),
        )
        .with_for_update(skip_locked=True)
    )
    if job_id is not None:
        statement = statement.where(ObjectCleanupJob.id == job_id)
    statement = statement.order_by(
        func.coalesce(ObjectCleanupJob.not_before, ObjectCleanupJob.created_at),
        ObjectCleanupJob.created_at,
        ObjectCleanupJob.id,
    ).limit(limit)
    jobs = list(session.scalars(statement))
    for job in jobs:
        job.status = "processing"
        job.not_before = None
    session.commit()
    return jobs


def _retry_backoff(attempts: int) -> timedelta:
    exponent = max(0, min(attempts - 1, 30))
    delay_seconds = _RETRY_BACKOFF_BASE.total_seconds() * (2**exponent)
    return timedelta(seconds=min(delay_seconds, _RETRY_BACKOFF_MAX.total_seconds()))


def recover_stale_import_jobs(session: Session, *, limit: int = 100) -> list[ImportJob]:
    stale_before = datetime.now(UTC) - IMPORT_LEASE
    jobs = list(
        session.scalars(
            select(ImportJob)
            .where(
                ImportJob.status == "pending",
                ImportJob.created_at <= stale_before,
            )
            .order_by(ImportJob.created_at, ImportJob.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
    )
    now = datetime.now(UTC)
    for job in jobs:
        job.status = "failed"
        job.summary = {**job.summary, "recovered_stale": 1}
        job.errors = [
            *job.errors,
            {
                "sheet": "Workbook",
                "row": 0,
                "field": "transaction",
                "message": "导入进程中断且已超出租约；未提交的商品事务已回滚",
            },
        ]
        job.completed_at = now
    session.commit()
    return jobs
