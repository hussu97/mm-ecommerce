from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from app.models.order import DeliveryMethodEnum, OrderStatusEnum

# Re-exported so `from app.schemas.order import PaymentMethodEnum` keeps working.
# It is defined next to the model rather than here because it is now a domain
# value with a column behind it — `card` versus `cod` — and not a shape the API
# happens to accept. See `app/models/payment_gateway.py` for why `stripe` is
# still in it.
from app.models.payment_gateway import PaymentMethodEnum  # noqa: F401
from app.schemas.courier import CourierBadge

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
    #: What the customer asked to have written on this line, if anything.
    personalisation_note: str | None = None


class OrderCreate(BaseModel):
    #: Where the confirmation goes, and half of who this customer is.
    #:
    #: This was optional, on the reasoning that a phone number is enough to run
    #: a delivery and that demanding an address at checkout cost more orders
    #: than it saved. That trade has been re-taken and reversed, for two
    #: reasons the old rule could not answer:
    #:
    #: * every order gets written confirmation. An order the customer has no
    #:   record of is one they ring the shop about, and the mailer refuses to
    #:   write to the `…@guest.local` placeholder that stood in for a missing
    #:   address — so "optional" meant "silently no confirmation".
    #: * the new-customer coupon is refused on identity, and a missing email
    #:   left the phone number as the only thing holding the rule up. Two
    #:   identities are meaningfully harder to churn through than one.
    #:
    #: Required rather than defaulted, so a client that omits it is told so
    #: instead of quietly buying a placeholder — see `_email_is_not_optional`.
    email: EmailStr
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

    @model_validator(mode="before")
    @classmethod
    def _email_is_not_optional(cls, data: Any) -> Any:
        """
        Say *why* the order was refused, in words a customer can act on.

        Email has only just become required, and a browser holding the previous
        bundle still posts a complete checkout with no `email` key in it — or
        with the null or empty string the old optional field accepted. Left to
        Pydantic those are three different complaints ("Field required", "Input
        should be a valid string", "value is not a valid email address"), and
        the storefront's client joins the `msg` of each 422 entry straight into
        the toast the customer reads. None of them names the field, and none of
        them is an instruction anybody can follow.

        There is no `RequestValidationError` handler to fix this centrally, and
        adding one would re-word every 422 the API has ever returned. So this
        one field states its own case: `PydanticCustomError` replaces the
        message verbatim, with none of the "Value error, " prefix a plain
        `ValueError` would carry, and the response stays the ordinary 422 shape
        every client already understands.
        """
        if isinstance(data, dict):
            given = data.get("email")
            if given is None or (isinstance(given, str) and not given.strip()):
                raise PydanticCustomError(
                    "missing",
                    "Please enter your email address — your order confirmation "
                    "is sent to it.",
                )
        return data


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
    #: How much has been sent back to the card, in the order's currency. Zero on
    #: everything that has not been refunded, which is almost every order.
    #:
    #: On the response because the cancelled and undelivered emails read it: the
    #: customer is told the actual figure that left our account rather than a
    #: promise that "any payment taken is being returned", which was true and
    #: unhelpfully vague.
    #:
    #: Coerced from None below: the column is NOT NULL with a default, so a row
    #: read from the database always has a number, but an Order built in memory
    #: and not yet flushed reads None — and the confirmation email is rendered
    #: from exactly such an object, inside the same request that created it.
    refunded_amount: float = 0
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
    #: The customer's name and number, shown together wherever either shows. A
    #: website order carries these on `shipping_address_snapshot` and may leave
    #: them null here; an aggregator or counter order carries them here.
    customer_name: str | None = None
    #: E.164 ("+971501234567"), normalised on every write path. The dial code is
    #: inside it; `customer_phone_country` (ISO "AE") and `customer_phone_type`
    #: ("mobile"/"landline"/"toll_free") sit beside it for readability.
    customer_phone: str | None = None
    customer_phone_country: str | None = None
    customer_phone_type: str | None = None
    #: A code to enter after dialling the number to reach the customer — Deliveroo
    #: only, kept apart from the number and joined by the client for display.
    customer_phone_access_code: str | None = None
    #: For an aggregator order (`source == "aggregator"`), the channel it came
    #: in on. Drives the admin channel tab/badge and is null on everything else.
    aggregator_channel: str | None = None
    #: The delivery charge the marketplace showed the customer on an aggregator
    #: order — theirs, not ours, and kept out of `delivery_fee`. Shown on the
    #: receipt only; null on everything else.
    aggregator_delivery_fee: float | None = None
    #: The short driver-facing pickup code for an aggregator order (Talabat
    #: "1445", the GrubOps sequence where none exists). Null on everything else.
    aggregator_display_code: str | None = None
    #: The aggregator's own rider on an aggregator order, as GrubOps reports it —
    #: a name, a mobile, and its delivery-job status verbatim. MM books no courier
    #: for these, so this is all the driver detail there is (no live GPS, hence no
    #: distance); the packed panel shows it beside the channel logo. Null until a
    #: rider is assigned, and on every non-aggregator order.
    aggregator_driver_name: str | None = None
    aggregator_driver_phone: str | None = None
    aggregator_driver_status: str | None = None
    #: The marketplace's own long order id for an aggregator order, for support
    #: and tracing. Null on everything else.
    external_reference: str | None = None
    #: Who is carrying it, as a badge (code, name, logo). Filled for an
    #: aggregator order from its channel; null for a website order here (the
    #: admin's `OrderDeliveryResponse.courier` carries the dispatched courier).
    courier: CourierBadge | None = None
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

    @field_validator("refunded_amount", mode="before")
    @classmethod
    def _no_refund_is_zero(cls, value: Any) -> Any:
        """
        None means nothing was refunded, not "invalid".

        The column is NOT NULL with a default, so a row read back from the
        database always carries a number. An `Order` built in memory and not yet
        flushed does not — and the confirmation email is rendered from exactly
        such an object, inside the same request that created it.
        """
        return 0 if value is None else value

    @model_validator(mode="after")
    def _fill_courier(self) -> "OrderResponse":
        """Derive the carrier badge from the channel when nothing set it.

        Only an aggregator order resolves here — a website order's courier lives
        on the admin delivery record, not on the customer-facing order.
        """
        if self.courier is None:
            self.courier = CourierBadge.for_order(
                source=self.source, aggregator_channel=self.aggregator_channel
            )
        return self


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
    #: Shown beneath the name in the orders list (E.164). Its country/type and any
    #: Deliveroo access code sit alongside; the client joins the code for display.
    customer_phone: str | None = None
    customer_phone_country: str | None = None
    customer_phone_type: str | None = None
    customer_phone_access_code: str | None = None
    delivery_fee: float | None = None
    low_order_fee: float | None = None
    #: For an aggregator order, the channel it came in on ("Talabat", "Keeta
    #: 2.0"…), so the list can show the marketplace logo in place of a bare
    #: "Website" badge. Null on web/counter.
    aggregator_channel: str | None = None
    #: Who is carrying it, as a badge (code, name, logo) — filled for an
    #: aggregator order from its channel, so the list renders the courier logo.
    courier: CourierBadge | None = None

    # ── is this order paying for itself? ─────────────────────────────────────
    #
    # Computed in SQL by `order_service.get_all_admin` rather than by calling
    # `order_economics` per row: the list serves pages of up to two thousand,
    # and a per-row service call is two thousand round-trips for one column.
    # The arithmetic is the same, and a test pins them together.

    #: What is left after the cost of sale, the payment fee and any refund.
    #: Null when a cost of sale exists but has not been told to us — an
    #: aggregator whose commission rate is not configured.
    net_value: float | None = None
    #: `net_value` as a share of the goods at menu price, before discount.
    cost_cover: float | None = None
    #: Whether the row cleared the shop's direct-cost bar. **Three-valued**: null
    #: is "cannot say", and the console must render it as a dash rather than a
    #: cross — an unconfigured rate is not a loss-making order.
    covers_direct_cost: bool | None = None

    @model_validator(mode="after")
    def _fill_courier(self) -> "OrderListResponse":
        if self.courier is None:
            self.courier = CourierBadge.for_order(
                source=self.source, aggregator_channel=self.aggregator_channel
            )
        return self


class OrderEconomicsResponse(BaseModel):
    """
    What the shop kept on one order, for the admin who has to decide whether the
    order was worth taking.

    Admin-only and deliberately its own response rather than fields on
    `OrderResponse`: what a courier cost us and what a processor keeps are not
    the customer's business, and the separation is what keeps that a property of
    the type rather than a rule somebody has to remember — the same reasoning
    that keeps `OrderDeliveryResponse` out of the customer's payload.
    """

    #: What the customer paid, fees included.
    charged: float
    #: What they paid for goods — `charged` less delivery and low-order fees.
    items_value: float
    #: What the courier cost, where one was booked and has told us. Null on a
    #: third-party zone: nobody invoices us per order there, which is a real
    #: "we do not know" rather than a zero.
    courier_cost: float | None
    #: What the marketplace took on an aggregator order — its cost of sale, in
    #: place of the courier cost it never has. Null on a website or counter
    #: order, and on an aggregator channel whose rate is not configured yet.
    aggregator_fee: float | None
    processing_fee: float
    #: Whether `processing_fee` is the gateway's published rate or its invoice.
    #: Stripe's real figure lives on the balance transaction and does not exist
    #: until the charge settles.
    processing_fee_is_estimated: bool
    refunded: float
    net: float
    #: Net as a share of everything the customer handed over, and of the goods
    #: alone. Null when the base is zero — a full-discount order has no
    #: percentage, and 0 would read as "we kept none of it".
    margin_on_charged: float | None
    margin_on_items: float | None

    #: The goods at menu price, before discount, and net as a share of it.
    #: The denominator is held still on purpose: measuring against what was
    #: actually charged would hide a discounted order's losses inside a smaller
    #: base. Null when there were no goods.
    items_before_discount: float
    cost_cover: float | None
    #: Whether the order cleared `direct_cost_threshold`. **Three-valued**:
    #: null means the answer is unknowable — an aggregator whose commission
    #: rate nobody has configured yet — and must not be rendered as a failure.
    covers_direct_cost: bool | None
    #: The bar `covers_direct_cost` was judged against, sent so the console
    #: labels its own column rather than hard-coding a number that would then
    #: exist in two places.
    direct_cost_threshold: float
