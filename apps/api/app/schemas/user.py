from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    phone: str | None = None
    #: The language they signed up in, so the welcome lands in it. There is no
    #: order to read one off here, and a `User` records no preference, so the
    #: request is the only thing that knows.
    locale: str | None = Field(None, max_length=5)
    #: The Turnstile solution from the form. Optional in the schema so a
    #: deployment with no secret configured keeps working unchanged, and so the
    #: refusal comes from `turnstile_service` with one message rather than from
    #: Pydantic with a field-shaped one that tells a bot where to look.
    turnstile_token: str | None = Field(None, max_length=2048)


class UserUpdate(BaseModel):
    phone: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    phone: str | None
    is_active: bool
    is_admin: bool
    is_guest: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GuestSessionRequest(BaseModel):
    email: EmailStr | None = None


class PasswordResetRequest(BaseModel):
    email: EmailStr
    #: The language the request came from, so the reset link and the email
    #: around it are both in it.
    locale: str | None = Field(None, max_length=5)
    #: See `UserCreate.turnstile_token`.
    turnstile_token: str | None = Field(None, max_length=2048)


class PasswordResetConfirm(BaseModel):
    token: str
    new_password: str = Field(min_length=8)
