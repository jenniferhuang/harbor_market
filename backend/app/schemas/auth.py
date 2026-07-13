from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


def normalize_username(value: str) -> str:
    return value.strip().casefold()


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username_field(cls, value: object) -> object:
        return normalize_username(value) if isinstance(value, str) else value


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str = Field(
        min_length=3,
        max_length=50,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    )
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username_field(cls, value: object) -> object:
        return normalize_username(value) if isinstance(value, str) else value


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    created_at: datetime
    last_login_at: datetime | None


class UserResponse(BaseModel):
    data: UserPublic


class MessageData(BaseModel):
    message: str


class MessageResponse(BaseModel):
    data: MessageData


class HealthData(BaseModel):
    status: Literal["ok"]
    database: Literal["ok"]


class HealthResponse(BaseModel):
    data: HealthData


class ErrorField(BaseModel):
    field: str
    message: str


class ErrorData(BaseModel):
    code: str
    message: str
    fields: list[ErrorField] | None = None


class ErrorResponse(BaseModel):
    error: ErrorData
    detail: list[ErrorField] | None = None
