"""
The wire shape of `POST /orders/preview`: what an order *would* be written with.

The checkout used to work its money out for itself — the grand total twice in
one file from different inputs, a small-basket fee mirroring `low_order_fee_for`
line for line, and a VAT figure from `(subtotal - discount) * 5 / 105` that
ignored both fees, so the "VAT included" line was not 5/105 of the total printed
immediately above it. Four numbers on the most expensive screen on the site, all
derived by a browser, all obliged to agree with a server they could not see.

They are one response now. Everything below is rendered, never recomputed; the
checkout's only job is to draw it. The endpoint runs the same
`order_pricing.compute_order_totals` that `create_order` runs, so the figures
here are the figures the order gets — see `order_service.preview_order`.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.models.order import DeliveryMethodEnum
from app.schemas.delivery import DeliveryQuoteResponse


class OrderPreviewRequest(BaseModel):
    """
    Everything the checkout knows, which is deliberately less than an order.

    Flat coordinates rather than an `AddressCreate`, because a form being filled
    in does not have a valid address yet — a customer who has dropped a pin but
    not typed their surname still needs a total on screen. The pin is all that
    prices a delivery anyway.
    """

    delivery_method: DeliveryMethodEnum
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    #: The pin's formatted address. Passed to the courier so its own estimate is
    #: taken against the same place the driver would be sent to — and, where the
    #: fee *is* that estimate, against the place it is charged for.
    address: str | None = None
    promo_code: str | None = None
    #: Guest checkout: which basket to price. Matches `OrderCreate.session_id`,
    #: and falls back to the `X-Session-Id` header the rest of the storefront
    #: sends.
    session_id: str | None = None
    #: The email typed into the form, if any. Only ever used to judge a coupon —
    #: a new-customer code is refused on an address that has ordered before, and
    #: a preview that did not send it would show a discount the pay button then
    #: refuses.
    email: str | None = None
    #: Likewise. The phone gate is reported here and enforced at order creation.
    phone: str | None = None


class OrderPreviewPromo(BaseModel):
    """
    What the server makes of the code on this basket.

    Carried on the preview rather than left to a second round trip because the
    discount is part of the total: a screen that renders a server total and a
    client discount can print two figures that do not subtract to each other.
    """

    code: str
    valid: bool
    #: The server's own words when it refuses. The checkout shows them verbatim
    #: — "no longer valid" with no reason reads as a bug rather than a rule.
    message: str | None = None
    discount_amount: float = 0.0
    #: Whether this coupon carries the phone gate at all, and whether the number
    #: we were given satisfies it. Outstanding is exactly
    #: `requires_phone_verification and not phone_verified`; see
    #: `PromoCodeValidateResponse`, whose two fields these mirror.
    requires_phone_verification: bool = False
    phone_verified: bool = True


class OrderPreviewResponse(BaseModel):
    """
    The exact totals an order placed right now would carry.

    Read it as a receipt that has not happened yet. Every field is final except
    where `serviceable` or `delivery_fee` says otherwise.
    """

    subtotal: float
    discount_amount: float
    #: Null when there is no fee to name: nowhere we can deliver to, or no pin
    #: dropped yet. The checkout renders "—" or "fee from your address"
    #: accordingly, which is what it has always done — and `total` then excludes
    #: delivery, exactly as the screen has always shown it. An order is never
    #: written in this state: `create_order` refuses the address instead.
    delivery_fee: float | None = None
    #: The small-basket surcharge. Zero on everything above the threshold, on
    #: every collection, and on an empty basket.
    low_order_fee: float = 0.0
    #: The rate stamped on the order: the basket's single rate where it has one,
    #: the blended effective rate on a mixed basket.
    vat_rate: float
    #: VAT on the goods only. Both fees sit outside it, per UAE rules — so this
    #: is *not* 5/105 of `total`, and the checkout no longer pretends it is.
    vat_amount: float
    total_excl_vat: float
    total: float
    #: Null when no code was sent. Present and `valid=false` when one was and the
    #: server refused it — the discount is then zero and the totals above already
    #: reflect that.
    promo: OrderPreviewPromo | None = None
    #: The same delivery answer `POST /delivery/quote` gives, from the same
    #: pricing call these totals came out of. Folded in rather than fetched
    #: separately because `delivery_service.price` reaches a courier's live
    #: pricing API: two endpoints meant two quotes for one basket, and two
    #: chances to show a fee we did not charge.
    delivery: DeliveryQuoteResponse = Field(
        description="Fee, free-delivery countdown and arrival estimate for this pin."
    )
