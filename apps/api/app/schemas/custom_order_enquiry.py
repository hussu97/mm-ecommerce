from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
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
    #: The photos, as the `key` each upload to `/enquiry/image` returned
    #: (``enquiries/pending/<uuid>.<ext>``). Named for what it held before photos
    #: went private; still takes that upload's signed `url` so a storefront
    #: deployed ahead of or behind the API keeps working. Anything that is not
    #: one of our own pending uploads is a 400 (`custom_order_enquiry_service`).
    reference_image_urls: list[Annotated[str, Field(max_length=2048)]] = Field(
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


class EnquiryImageUploadResponse(BaseModel):
    """One uploaded inspiration photo, not yet attached to an enquiry.

    `key` is what the form sends back in `reference_image_urls`. `url` is a
    short-lived signed link (None if one could not be minted) — the storefront
    previews from the local file, so it must not depend on it.
    """

    key: str
    url: str | None = None


class CustomOrderEnquiryResponse(BaseModel):
    """What the storefront gets back: enough to confirm receipt, nothing more."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_at: datetime


class CustomOrderEnquiryAdminResponse(BaseModel):
    """The whole lead, for the admin Enquiries list."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    customer_name: str
    customer_phone: str
    description: str
    approx_kg: Decimal | None
    #: Short-lived signed links to the photos, minted for this request and never
    #: stored. A photo that could not be signed is left out.
    reference_image_urls: list[str]
    delivery_by: date | None
    created_at: datetime


class PaginatedCustomOrderEnquiries(BaseModel):
    items: list[CustomOrderEnquiryAdminResponse]
    total: int
    page: int
    per_page: int
    pages: int
