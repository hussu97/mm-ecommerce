from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.order import DeliveryMethodEnum, OrderStatusEnum

# Re-exported so `from app.schemas.order import PaymentMethodEnum` keeps working.
# It is defined next to the model rather than here because it is now a domain
# value with a column behind it — `card` versus `cod` — and not a shape the API
# happens to accept. See `app/models/payment_gateway.py` for why `stripe` is
# still in it.
from app.models.payment_gateway import PaymentMethodEnum  # noqa: F401

from .address import AddressCreate
from .fulfilment import FulfilmentResponse


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    product_id: UUID | None
    product_name: str
    product_sku: str
    product_translations: dict[str, dict[str, str]] = {}
    quantity: int
    base_price: float
    options_price: float
    unit_price: float
    total_price: float
    selected_options_snapshot: list[Any] = []


class OrderCreate(BaseModel):
    # Optional: a phone number is enough to run a delivery. Email is only
    # needed if the customer wants the confirmation in writing, so asking for
    # it as a hard requirement cost more orders than it saved.
    email: EmailStr | None = None
    delivery_method: DeliveryMethodEnum
    shipping_address: AddressCreate | None = (
        None  # required if delivery_method == delivery
    )
    #: Which branch the customer is coming to. Only read for a pickup order, and
    #: optional even then: an order placed by a client that predates the picker
    #: still has to be accepted, and falls back to the branch the shop would have
    #: resolved for it anyway.
    pickup_branch_id: UUID | None = None
    #: The language the checkout was in. Everything the shop writes about this
    #: order is written in it. Anything unrecognised is normalised to English by
    #: `order_service` rather than refused — a bad locale must not lose a sale.
    locale: str | None = Field(None, max_length=5)
    promo_code: str | None = None
    #: What the customer chose, not who will process it. `stripe` is still
    #: accepted because a browser holding the previous bundle sends it and means
    #: `card`; `order_service` normalises it before the row is written.
    payment_method: PaymentMethodEnum = Field(
        description="card | cod (legacy aliases: stripe, tabby, tamara)"
    )
    notes: str | None = None
    # Guest checkout: identify which cart to convert
    session_id: str | None = None


class OrderStatusUpdate(BaseModel):
    status: OrderStatusEnum
    admin_notes: str | None = None


class OrderStatusStamp(BaseModel):
    """When this order reached one status.

    Two fields and no more. The row behind it also carries who moved the order
    and from where, and none of that belongs in a payload the customer's own
    order page reads — same reasoning as `OrderDeliveryResponse` being kept out
    of `OrderResponse`. The admin's `GET /orders/{n}/status-events` is where the
    rest of the row is served.
    """

    model_config = ConfigDict(from_attributes=True)

    status: str
    at: datetime


class OrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    user_id: UUID | None
    email: str
    delivery_method: DeliveryMethodEnum
    delivery_fee: float
    #: The small-basket surcharge. Zero on most orders; its own field rather
    #: than folded into `delivery_fee` so the storefront can label it honestly
    #: and explain why it is there.
    low_order_fee: float = 0.0
    subtotal: float
    discount_amount: float
    total: float
    status: OrderStatusEnum
    promo_code_used: str | None
    shipping_address_snapshot: dict[str, Any] | None
    payment_method: str | None
    payment_provider: str | None
    payment_id: str | None
    vat_rate: float
    vat_amount: float
    total_excl_vat: float
    notes: str | None
    admin_notes: str | None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse] = []
    #: Whether this order's email already belongs to a real account.
    #:
    #: Only ever true for a signed-up user — a guest's generated `@guest.local`
    #: identity is not an account anyone can sign in to. It rides on the order
    #: rather than living behind an "is this email registered?" endpoint,
    #: because such an endpoint answers that question for *any* address; this
    #: one only answers it for someone who already holds the order number and
    #: the email that placed it.
    email_has_account: bool = False
    #: How the order reaches this customer — when to expect it, where to collect
    #: it, and a live map when there is one. Null on a response built without a
    #: session to load it from (the POS path, and anything constructed in a
    #: test); every customer-facing route fills it in.
    #:
    #: Deliberately narrow. The admin's `OrderDeliveryResponse` carries the
    #: courier, the driver and what the run cost; none of that is here, and the
    #: separation is what keeps "the customer is not told who carries their
    #: cake" a property of the type rather than a rule someone has to remember.
    fulfilment: FulfilmentResponse | None = None
    #: Which channel rang this up — `online` for the storefront, `cashier` for
    #: the counter. Carried so the mailer can tell them apart: a counter sale is
    #: handed over across the counter with a printed receipt, and has no use for
    #: "your order is confirmed".
    #:
    #: Null only on a response built by hand in a test — the column itself is
    #: NOT NULL. Nothing customer-facing renders it; it is here because the
    #: alternative was for each of the mailer's callers to remember the rule.
    source: str | None = None
    #: The language this order was placed in, and the one every email about it
    #: is written in.
    locale: str = "en"
    #: When this order reached each status, oldest first, at most one entry per
    #: status.
    #:
    #: The timeline used to source four of its five stamps from
    #: `order_deliveries`, which only an integrated courier ever fills — so a
    #: pickup order, a third-party zone or an order somebody walked through by
    #: hand showed `created` and four blanks. These come from the order's own
    #: history and exist whoever carried it.
    #:
    #: The *latest* row per status rather than the first: an order that went
    #: undelivered and was re-dispatched should show the dispatch that is
    #: actually happening, not the one that failed.
    status_history: list[OrderStatusStamp] = []


class OrderListResponse(BaseModel):
    """
    One row on the admin's orders screen, whichever channel it came from.

    Web and POS orders share one table, so they share one row shape. The
    channel-specific fields are null for the other channel rather than split
    across two models — a single list that has to union two shapes is how the
    admin ended up with two screens in the first place.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    order_number: str
    email: str
    status: OrderStatusEnum
    total: float
    delivery_method: DeliveryMethodEnum
    payment_provider: str | None
    created_at: datetime
    item_count: int = 0

    # ── which channel, and what that channel needs shown ──────────────────────
    #: `online` for the storefront, `cashier` for the counter. Null on orders
    #: placed before the two channels shared a table.
    source: str | None = None
    #: The counter lifecycle, which is a different shape from `status`.
    pos_status: str | None = None
    order_type: str | None = None
    #: The kitchen. Null for an order placed before zones named a branch.
    branch_id: UUID | None = None
    #: The short number the counter calls out — "order 12".
    check_number: int | None = None
    customer_name: str | None = None
    delivery_fee: float | None = None
    low_order_fee: float | None = None
