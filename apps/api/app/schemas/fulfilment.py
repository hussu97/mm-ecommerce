"""
The customer-facing shape of "how does this order reach me".

Serialisation only — every decision behind these fields lives in
`app.services.fulfilment_service`, including the rule that a courier's name and
a driver's identity never appear here.

Nothing in this module imports a service. `OrderResponse` embeds
`FulfilmentResponse`, and `order_service` imports `OrderResponse`, so a service
import here would close a cycle through `app.services.__init__`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel

from app.models.branch import Branch

if TYPE_CHECKING:  # pragma: no cover — typing only, see the module docstring
    from app.services.fulfilment_service import Fulfilment

__all__ = ["FulfilmentResponse", "PickupBranchResponse"]


class PickupBranchResponse(BaseModel):
    """A place to collect from, in both locales at once.

    Both languages ride on one object rather than the API picking one: the same
    payload is read by an English storefront, an Arabic storefront and an email
    that has no locale at all, and a server that guesses wrong is harder to fix
    than a client that chooses. The Arabic fields are null when they would only
    repeat the English, so a renderer can show a second line when there is a
    second line to show.
    """

    id: UUID
    reference: str
    name: str
    name_ar: str | None = None
    address: str | None = None
    address_ar: str | None = None
    city: str | None = None
    city_ar: str | None = None
    phone: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    #: Google Maps, built from the branch's own pin rather than its address text.
    maps_url: str | None = None
    opening_from: str
    opening_to: str

    @classmethod
    def of(cls, branch: Branch) -> "PickupBranchResponse":
        return cls(
            id=branch.id,
            reference=branch.reference,
            name=branch.name,
            name_ar=_second(branch.name_for("ar"), branch.name),
            address=branch.address,
            address_ar=_second(branch.address_for("ar"), branch.address),
            city=branch.city,
            city_ar=_second(branch.city_for("ar"), branch.city),
            phone=branch.phone,
            latitude=float(branch.latitude) if branch.latitude is not None else None,
            longitude=float(branch.longitude) if branch.longitude is not None else None,
            maps_url=branch.maps_url,
            opening_from=branch.opening_from,
            opening_to=branch.opening_to,
        )


def _second(translated: Any, original: Any) -> str | None:
    """The Arabic value, or None when it is only the English one again."""
    if not translated or translated == original:
        return None
    return str(translated)


class FulfilmentResponse(BaseModel):
    method: str
    stage: str
    estimated_at: datetime | None = None
    precision: str | None = None
    tracking_url: str | None = None
    #: Whether the tracking link reaches the customer as a text message rather
    #: than from us. True only while a rider is carrying the parcel. Lets a
    #: renderer say "check your messages" instead of showing no link at all.
    tracking_by_sms: bool = False
    #: Whether a rider we can hear back from carries this order. Decides whether
    #: the storefront may say "we'll share a live link when it is collected".
    courier_managed: bool = False
    packed_at: datetime | None = None
    picked_up_at: datetime | None = None
    delivered_at: datetime | None = None
    branch: PickupBranchResponse | None = None

    @classmethod
    def of(cls, fulfilment: "Fulfilment") -> "FulfilmentResponse":
        return cls(
            method=fulfilment.method,
            stage=fulfilment.stage,
            estimated_at=fulfilment.estimated_at,
            precision=fulfilment.precision,
            tracking_url=fulfilment.tracking_url,
            tracking_by_sms=fulfilment.tracking_by_sms,
            courier_managed=fulfilment.courier_managed,
            packed_at=fulfilment.packed_at,
            picked_up_at=fulfilment.picked_up_at,
            delivered_at=fulfilment.delivered_at,
            branch=(
                PickupBranchResponse.of(fulfilment.branch)
                if fulfilment.branch is not None
                else None
            ),
        )
