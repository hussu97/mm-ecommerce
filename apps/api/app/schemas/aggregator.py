"""What the bootstrap/warmer worker sends when it hands a session to the API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AggregatorSessionPush(BaseModel):
    """A freshly captured marketplace session, pushed in for the ingest to replay.

    The worker that runs the browser (and can solve OTP / a bot sensor) sends
    the bundle here; the API seals it and stores it. The cookies carry the load-
    bearing anti-bot cookie, `header_profile` the exact request fingerprint.
    """

    channel: str
    account_ref: str = ""
    cookies: dict[str, str] = Field(default_factory=dict)
    tokens: dict = Field(default_factory=dict)
    header_profile: dict[str, str] = Field(default_factory=dict)
    token_expires_at: datetime | None = None
    cookie_expires_at: datetime | None = None


class AggregatorSessionResponse(BaseModel):
    """The stored session's health, echoed back to the worker."""

    model_config = ConfigDict(from_attributes=True)

    channel: str
    account_ref: str
    status: str
    token_expires_at: datetime | None = None
    cookie_expires_at: datetime | None = None
    last_bootstrap_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
