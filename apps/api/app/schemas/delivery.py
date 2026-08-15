"""
What the storefront is told about delivery.

These models used to live inside `api/v1/delivery.py`, which was fine while one
router served them. `POST /orders/preview` now answers the same question as part
of a larger one — it prices the whole order off a single call to
`delivery_service.price`, so the checkout gets the fee, the free-delivery
countdown and the arrival estimate from the same pricing it gets the total from,
rather than asking the courier twice. Two routers serving one shape means the
shape belongs in `schemas`.
"""

from __future__ import annotations

from pydantic import BaseModel


class DeliveryEstimateResponse(BaseModel):
    #: ISO 8601, on the shop's clock.
    at: str
    precision: str


class DeliveryQuoteResponse(BaseModel):
    #: Null until there is something to price — no pin yet, or a pin nothing can
    #: be delivered to. `serviceable` is what tells the two apart.
    delivery_fee: float | None = None
    base_fee: float | None = None
    free_delivery_applied: bool
    #: Whether free delivery reaches this pin at all. False in the areas priced
    #: from a live courier quote — there is no fee of ours to waive there, only
    #: a bill that arrives whatever the basket is worth.
    free_delivery_available: bool = True
    #: The basket that earns free delivery *at this pin*. Thresholds vary by
    #: zone, so this is the zone's own number wherever one has resolved.
    #:
    #: **Null before a pin exists.** Thresholds stopped being national when the
    #: outer zones went fixed-fee — every zone answers for itself now, so with no
    #: pin there is genuinely no figure to name. This field was left as a
    #: required `float` when `price()` changed, and since FastAPI validates the
    #: response, a quote with no pin raised `ResponseValidationError` and the
    #: checkout got a 500 on its very first call. The storefront had already
    #: been written for `number | null`; only this line disagreed.
    free_threshold: float | None = None
    #: True when there is no resolved zone behind the threshold above — i.e. no
    #: pin has been dropped, and `free_threshold` is therefore null. Copy driven
    #: by it must stay hedged until it turns false. Defaulted so an older client
    #: that does not read it behaves exactly as before.
    free_threshold_provisional: bool = False
    remaining_for_free: float
    zone_name: str | None = None
    in_known_zone: bool
    #: When the order should arrive, and how precisely. `precision` is "time"
    #: where the schedule is ours to read and "day" where it is not. Null until
    #: there is a pin to read it from.
    delivery_estimate: DeliveryEstimateResponse | None = None
    #: False when we cannot deliver to this pin at all. The checkout says so and
    #: refuses to submit; the API refuses the order too, so the two agree.
    serviceable: bool = True
