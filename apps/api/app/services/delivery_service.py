from __future__ import annotations

from decimal import Decimal

from app.models.address import RegionEnum
from app.models.order import DeliveryMethodEnum

# Regions with standard (lower) delivery rate
STANDARD_ZONE = {
    RegionEnum.DUBAI,
    RegionEnum.SHARJAH,
    RegionEnum.AJMAN,
}

STANDARD_RATE = Decimal("35.00")
REMOTE_RATE = Decimal("50.00")
FREE_THRESHOLD = Decimal("200.00")

DELIVERY_RATES = {
    "standard_zones": [e.value for e in STANDARD_ZONE],
    "standard_rate": float(STANDARD_RATE),
    "remote_zones": [e.value for e in RegionEnum if e not in STANDARD_ZONE],
    "remote_rate": float(REMOTE_RATE),
    "free_threshold": float(FREE_THRESHOLD),
    "pickup_rate": 0.0,
}


def calculate_fee(
    delivery_method: DeliveryMethodEnum,
    region: RegionEnum | None,
    subtotal: Decimal,
) -> Decimal:
    """Return the delivery fee in AED."""
    if delivery_method == DeliveryMethodEnum.PICKUP:
        return Decimal("0.00")

    if subtotal >= FREE_THRESHOLD:
        return Decimal("0.00")

    if region in STANDARD_ZONE:
        return STANDARD_RATE

    return REMOTE_RATE
