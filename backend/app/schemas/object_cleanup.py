from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ObjectCleanupJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_by: int | None
    object_key: str
    reason: str
    status: str
    attempts: int
    last_error: str | None
    not_before: datetime | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ObjectCleanupJobResponse(BaseModel):
    data: ObjectCleanupJobRead


class ObjectCleanupJobListResponse(BaseModel):
    data: list[ObjectCleanupJobRead]
