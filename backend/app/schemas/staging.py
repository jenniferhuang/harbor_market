from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class StagedProductImageRead(BaseModel):
    object_key: str
    mime_type: str
    size_bytes: int
    width: int
    height: int
    expires_at: datetime


class StagedProductImageResponse(BaseModel):
    data: StagedProductImageRead
