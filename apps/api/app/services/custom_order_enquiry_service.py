"""
Storing a custom-order enquiry — and nothing more.

An enquiry is a message, not an order; the shop reads it and decides, and if it
goes ahead converts it into a custom order (`orders.custom_order_service`). So
all this does is normalise the phone number and write the row. It `flush()`es and
lets the request dependency commit, per the transaction convention.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.phone import normalise_phone
from app.models.custom_order_enquiry import CustomOrderEnquiry
from app.schemas.custom_order_enquiry import CustomOrderEnquiryCreate


async def create(
    db: AsyncSession, *, data: CustomOrderEnquiryCreate
) -> CustomOrderEnquiry:
    """Persist one enquiry. Creates no order and holds no slot."""
    enquiry = CustomOrderEnquiry(
        customer_name=data.customer_name,
        # Store a normalised number when we can recognise one, but never lose the
        # customer's own digits if we can't — a lead we can't call back is worse
        # than an oddly formatted one.
        customer_phone=normalise_phone(data.customer_phone) or data.customer_phone,
        description=data.description,
        approx_kg=data.approx_kg,
        reference_image_urls=list(data.reference_image_urls),
        delivery_by=data.delivery_by,
    )
    db.add(enquiry)
    await db.flush()
    await db.refresh(enquiry)
    return enquiry
