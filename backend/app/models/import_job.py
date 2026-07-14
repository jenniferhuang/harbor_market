from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.user import User


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default=text("'pending'"),
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    workbook_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    dry_run: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )
    summary: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )
    errors: Mapped[list[Any]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    creator: Mapped[User] = relationship(back_populates="import_jobs")

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'validated', 'completed', 'failed')",
            name="status_allowed",
        ),
        CheckConstraint(
            "length(trim(original_filename)) BETWEEN 1 AND 255",
            name="original_filename_length",
        ),
        CheckConstraint(
            "length(workbook_sha256) = 64",
            name="workbook_sha256_length",
        ),
        CheckConstraint(
            "idempotency_key IS NULL OR length(idempotency_key) BETWEEN 8 AND 128",
            name="idempotency_key_length",
        ),
        Index(
            "uq_import_jobs_creator_idempotency_key",
            "created_by",
            "idempotency_key",
            unique=True,
        ),
        Index("ix_import_jobs_workbook_sha256", "workbook_sha256"),
        Index("ix_import_jobs_creator_created", "created_by", "created_at"),
        Index("ix_import_jobs_status_created", "status", "created_at"),
    )
