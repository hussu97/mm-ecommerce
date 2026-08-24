"""Schemas for report actions triggered from the console."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

_DATE = r"^\d{4}-\d{2}-\d{2}$"


class DailySalesEmailRequest(BaseModel):
    """Send the daily sales spreadsheet for a window to a set of recipients."""

    date_from: str = Field(pattern=_DATE)
    date_to: str = Field(pattern=_DATE)
    recipients: list[EmailStr] = Field(min_length=1)

    @field_validator("date_to")
    @classmethod
    def _in_order(cls, value: str, info) -> str:
        start = info.data.get("date_from")
        if start and start > value:
            raise ValueError("date_from must not be after date_to")
        return value


class DailySalesEmailRecipientResult(BaseModel):
    recipient: str
    status: str
    error: str | None = None


class DailySalesEmailResponse(BaseModel):
    subject: str
    rows: int
    sent: list[DailySalesEmailRecipientResult]
