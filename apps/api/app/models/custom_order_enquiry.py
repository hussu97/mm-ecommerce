"""
Custom-order enquiries: the lead a customer sends from the storefront, before
anything is a booking.

This is deliberately **not** a `CustomOrder`. A `CustomOrder` occupies a slot on
the custom-cake calendar and enforces capacity, lead time and blackouts — it is
a booking the kitchen commits to. An enquiry is the message that arrives on the
"We cater to" section of the home page: a name, a number, a description and maybe
some inspiration photos. It creates no order, holds no slot, and is answered by a
human who then decides whether it becomes a real booking. All it has to do is be
stored so nothing is lost, and trigger the email that tells the shop it arrived.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import Date, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class CustomOrderEnquiry(Base, UUIDMixin, TimestampMixin):
    """One custom-order request from the storefront. A lead, never a booking."""

    __tablename__ = "custom_order_enquiries"

    #: Who to call back. Denormalised — the person has no account and this is all
    #: they gave us.
    customer_name: Mapped[str] = mapped_column(String(150), nullable=False)
    customer_phone: Mapped[str] = mapped_column(String(30), nullable=False)

    #: What they want, in their own words.
    description: Mapped[str] = mapped_column(Text, nullable=False)

    #: Rough size, if they offered one. Optional — most people describe a cake
    #: long before they know its weight.
    approx_kg: Mapped[Any | None] = mapped_column(Numeric(6, 2), nullable=True)

    #: Inspiration photos they uploaded (public GCS URLs), at most four. The
    #: single most useful thing an enquiry carries.
    reference_image_urls: Mapped[Any] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )

    #: The date they'd like it by. Nullable and, crucially, only a wish — the
    #: real delivery date is confirmed by a human after review, which the form
    #: tells the customer in as many words.
    delivery_by: Mapped[date | None] = mapped_column(Date, nullable=True)

    def __repr__(self) -> str:
        return f"<CustomOrderEnquiry {self.customer_name} {self.created_at:%Y-%m-%d}>"
