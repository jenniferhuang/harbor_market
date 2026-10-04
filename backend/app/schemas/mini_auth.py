from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MiniLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")


class MiniProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str = Field(min_length=1, max_length=64)


class MiniCustomerPublic(BaseModel):
    id: int
    nickname: str
    avatar_url: str | None


class MiniCustomerResponse(BaseModel):
    data: MiniCustomerPublic


class MiniLoginData(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime
    customer: MiniCustomerPublic


class MiniLoginResponse(BaseModel):
    data: MiniLoginData


class MiniLogoutData(BaseModel):
    logged_out: Literal[True] = True


class MiniLogoutResponse(BaseModel):
    data: MiniLogoutData
