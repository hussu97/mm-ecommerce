from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The form never accepts more than this many inspiration photos — the storefront
#: enforces it too, but the API is the boundary that has to hold.
MAX_REFERENCE_IMAGES = 4


class CustomOrderEnquiryCreate(BaseModel):
    """A custom-order request from the "We cater to" section. A lead, not an order."""

    customer_name: str = Field(min_length=1, max_length=150)
    customer_phone: str = Field(min_length=3, max_length=30)
    description: str = Field(min_length=1, max_length=5000)
    approx_kg: Decimal | None = Field(default=None, gt=0, le=1000)
    reference_image_urls: list[str] = Field(
        default_factory=list, max_length=MAX_REFERENCE_IMAGES
    )
    delivery_by: date | None = None
    #: Cloudflare Turnstile solution. Optional so the check is off wherever no
    #: secret is configured (dev, tests); required in practice in production.
    turnstile_token: str | None = Field(default=None, max_length=2048)

    @field_validator("customer_name", "description")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class CustomOrderEnquiryResponse(BaseModel):
    """What the storefront gets back: enough to confirm receipt, nothing more."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime
