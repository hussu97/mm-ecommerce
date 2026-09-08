"""`admin_notes` is internal and must never reach the customer (F-ORD-10).

It used to sit on the shared `OrderResponse`, so it was serialised to the
customer on every order read. It now lives only on the admin-only
`OrderAdminDetails`; its one legitimate customer-facing use — the note shown when
an order is cancelled — is carried by `cancellation_reason`, which `to_response`
fills from `admin_notes` ONLY on a settled (cancelled-family) order.
"""

from __future__ import annotations

from app.models.order import OrderStatusEnum
from app.schemas.order import OrderAdminDetails, OrderResponse
from app.services.orders.order_service import _CANCELLATION_NOTE_STATUSES


def test_admin_notes_is_off_the_customer_order_response():
    # The structural guarantee: the field cannot be serialised to a customer
    # because it is not on the model the customer reads.
    assert "admin_notes" not in OrderResponse.model_fields
    assert "cancellation_reason" in OrderResponse.model_fields
    # It moved to the admin-only enrichment.
    assert "admin_notes" in OrderAdminDetails.model_fields


def test_cancellation_reason_is_scoped_to_the_settled_states():
    # Exactly the storefront `SETTLED` set — the states on which the account page
    # shows a cancellation note. A live order is never one of them, so an internal
    # note written on an active order is never surfaced.
    assert _CANCELLATION_NOTE_STATUSES == {
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.PAYMENT_FAILED,
        OrderStatusEnum.REFUNDED,
        OrderStatusEnum.DISPUTED,
    }
    for live in (
        OrderStatusEnum.CREATED,
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.DELIVERED,
    ):
        assert live not in _CANCELLATION_NOTE_STATUSES
