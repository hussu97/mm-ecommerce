"""
The email and the checkout must name the same arrival for the same order.

They are computed by different code for good reasons — checkout has a zone and
no order, an email has an order and a courier record — and every time those two
have drifted the customer has caught it. MM-20260805-008 was quoted 19:00 on the
page and 17:25 in the inbox.

So the number both sides use is pinned here: the courier's own promise. A flat
constant reappearing in either is the failure this module exists to catch.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.courier import Courier
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services.delivery import fulfilment_service
from app.services.delivery.fulfilment_service import TZ

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

NOON_SEND = Courier(
    code="noon_send",
    name="noon Send",
    unbatched_promise_kind="minutes",
    unbatched_promise_minutes=60,
)


class _Db:
    """Answers the delivery, the branch and the courier."""

    def __init__(self, delivery, courier=None):
        self._delivery = delivery
        self._courier = courier

    async def execute(self, _stmt):
        delivery, courier = self._delivery, self._courier

        class _R:
            def scalars(self_inner):
                return SimpleNamespace(first=lambda: delivery)

            def scalar_one_or_none(self_inner):
                return courier

        return _R()

    async def get(self, _model, _pk):
        return None


def _order(status, **over):
    base = dict(
        id=uuid.uuid4(),
        status=status,
        delivery_method=DeliveryMethodEnum.DELIVERY,
        created_at=NOW - timedelta(minutes=30),
        updated_at=NOW,
        branch_id=None,
        promised_at=None,
        promised_precision=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _delivery(**over):
    """A stand-in for the `order_deliveries` row.

    `picked_up_at` and `delivered_at` are not here: they were a second copy of
    two moments `order_status_events` now holds, and the history is the source.
    Tests that need them pass `reached=` to `for_order`.
    """
    base = dict(
        provider="lalamove",
        original_provider=None,
        courier_status=None,
        share_link=None,
        dispatchable_at=None,
    )
    base.update(over)
    return SimpleNamespace(**base)


async def test_a_collected_order_counts_from_the_collection_less_the_allowance():
    """
    noon Send promises an hour from the order being placed, and ten of those
    minutes are the kitchen packing and the rider arriving. Once the rider has
    the box those minutes have happened, so fifty remain.
    """
    picked_up = NOW - timedelta(minutes=5)
    result = await fulfilment_service.for_order(
        _Db(_delivery(provider="noon_send"), courier=NOON_SEND),
        _order(OrderStatusEnum.OUT_FOR_DELIVERY),
        now=NOW,
        reached={"out_for_delivery": picked_up},
    )
    assert result.estimated_at == picked_up.astimezone(TZ) + timedelta(minutes=50)


async def test_an_estimate_never_renders_in_the_past():
    """
    A rider running two hours late would otherwise produce an email saying the
    parcel arrived an hour ago, which reads as a bug and invites a support call
    about an order that is simply late.
    """
    picked_up = NOW - timedelta(hours=3)
    result = await fulfilment_service.for_order(
        _Db(_delivery(provider="noon_send"), courier=NOON_SEND),
        _order(OrderStatusEnum.OUT_FOR_DELIVERY),
        now=NOW,
        reached={"out_for_delivery": picked_up},
    )
    assert result.estimated_at > NOW.astimezone(TZ)


@pytest.mark.parametrize("status", [OrderStatusEnum.CONFIRMED, OrderStatusEnum.PACKED])
async def test_a_third_party_promise_keeps_its_date_and_gains_the_closing_hour(status):
    """
    What the customer was told at checkout, sharpened rather than replaced: the
    same day, bounded at the hour the partner finishes. The date moving would be
    the shop quietly changing its mind.
    """
    promised = NOW + timedelta(days=1)
    result = await fulfilment_service.for_order(
        _Db(_delivery(provider="third_party")),
        _order(status, promised_at=promised, promised_precision="day"),
        now=NOW,
    )
    assert result.precision == "day_by"
    assert result.estimated_at.date() == promised.astimezone(TZ).date()
    assert result.estimated_at.hour == fulfilment_service.THIRD_PARTY_BY_HOUR
