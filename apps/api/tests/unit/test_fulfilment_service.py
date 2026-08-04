"""
When an order arrives, and what the customer is allowed to know about it.

The estimate is a matrix — three delivery arrangements crossed with the whole
lifecycle — and every cell of it is a promise made to somebody standing at a
door or a counter, so it is pinned here rather than left to whichever branch
happened to run.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models.branch import Branch
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.services import fulfilment_service
from app.services.fulfilment_service import TZ

#: A Tuesday lunchtime in Sharjah, so nothing under test straddles midnight.
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _order(
    *,
    status: OrderStatusEnum,
    method: DeliveryMethodEnum = DeliveryMethodEnum.DELIVERY,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    branch_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        delivery_method=method,
        created_at=created_at or NOW - timedelta(minutes=30),
        updated_at=updated_at or NOW,
        branch_id=branch_id,
    )


def _delivery(**overrides) -> SimpleNamespace:
    base = dict(
        provider="lalamove",
        courier_status=None,
        share_link=None,
        batch=None,
        dispatchable_at=None,
        picked_up_at=None,
        delivered_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Db:
    """A session that answers exactly two questions: the delivery, and a branch."""

    def __init__(self, delivery=None, branch=None):
        self._delivery = delivery
        self._branch = branch

    async def execute(self, _stmt):
        delivery = self._delivery

        class _Scalars:
            def first(self_inner):
                return delivery

        class _Result:
            def scalars(self_inner):
                return _Scalars()

        return _Result()

    async def get(self, _model, _pk):
        return self._branch


async def _fulfilment(order, delivery=None, branch=None):
    return await fulfilment_service.for_order(_Db(delivery, branch), order, now=NOW)


# ── stages ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status,method,expected",
    [
        (OrderStatusEnum.CREATED, DeliveryMethodEnum.DELIVERY, "preparing"),
        (OrderStatusEnum.CONFIRMED, DeliveryMethodEnum.DELIVERY, "preparing"),
        (OrderStatusEnum.PACKED, DeliveryMethodEnum.DELIVERY, "ready"),
        (OrderStatusEnum.OUT_FOR_DELIVERY, DeliveryMethodEnum.DELIVERY, "on_the_way"),
        (OrderStatusEnum.DELIVERED, DeliveryMethodEnum.DELIVERY, "delivered"),
        # The same status means a different word for a collection order, because
        # nobody delivered anything — somebody came and got it.
        (OrderStatusEnum.DELIVERED, DeliveryMethodEnum.PICKUP, "collected"),
        (OrderStatusEnum.CANCELLED, DeliveryMethodEnum.DELIVERY, "settled"),
        (OrderStatusEnum.REFUNDED, DeliveryMethodEnum.DELIVERY, "settled"),
        (OrderStatusEnum.PAYMENT_FAILED, DeliveryMethodEnum.DELIVERY, "settled"),
        (OrderStatusEnum.DISPUTED, DeliveryMethodEnum.DELIVERY, "settled"),
    ],
)
def test_the_stage_word_follows_the_status_and_the_method(status, method, expected):
    assert (
        fulfilment_service.estimate_state_of(_order(status=status, method=method))
        == expected
    )


@pytest.mark.asyncio
async def test_a_failed_handover_is_a_stage_of_its_own():
    """
    `undelivered` is not a status — the order stays exactly where it was,
    because it is still paid for and still ours to deliver. It exists only on
    the courier record, and it has to reach the customer as its own thing
    rather than as "out for delivery, still".
    """
    result = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(provider="noon_send", courier_status="undelivered"),
    )
    assert result.stage == "undelivered"
    # And no time, because nobody has agreed one.
    assert result.estimated_at is None
    assert result.precision is None


# ── the estimate ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_collection_order_is_ready_two_hours_after_it_is_placed():
    result = await _fulfilment(
        _order(status=OrderStatusEnum.CONFIRMED, method=DeliveryMethodEnum.PICKUP)
    )
    assert result.precision == "time"
    assert (
        result.estimated_at
        == (NOW - timedelta(minutes=30)).astimezone(TZ)
        + fulfilment_service.KITCHEN_PREP
    )


@pytest.mark.asyncio
async def test_a_packed_collection_order_is_ready_now_and_says_so_exactly():
    """Once it is on the counter there is nothing left to estimate."""
    packed_at = NOW - timedelta(minutes=5)
    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.PACKED,
            method=DeliveryMethodEnum.PICKUP,
            updated_at=packed_at,
        )
    )
    assert result.precision == "exact"
    assert result.estimated_at == packed_at.astimezone(TZ)


@pytest.mark.asyncio
async def test_a_third_party_zone_promises_a_day_and_never_an_hour():
    """
    Their van, their schedule. Naming an hour would be borrowing a precision
    that belongs to somebody else.
    """
    result = await _fulfilment(
        _order(status=OrderStatusEnum.CONFIRMED), _delivery(provider="third_party")
    )
    assert result.precision == "day"
    assert result.estimated_at.date() == (NOW + timedelta(days=1)).astimezone(TZ).date()


@pytest.mark.asyncio
async def test_an_order_with_no_delivery_record_at_all_is_treated_as_third_party():
    """A delivery placed before the row existed still has to answer the question."""
    result = await _fulfilment(_order(status=OrderStatusEnum.CONFIRMED), None)
    assert result.precision == "day"
    assert result.courier_managed is False


@pytest.mark.asyncio
async def test_a_batched_order_arrives_an_hour_after_its_run_leaves():
    dispatch_at = NOW + timedelta(hours=2)
    result = await _fulfilment(
        _order(status=OrderStatusEnum.PACKED),
        _delivery(batch=SimpleNamespace(dispatch_at=dispatch_at)),
    )
    assert result.precision == "time"
    assert (
        result.estimated_at
        == dispatch_at.astimezone(TZ) + fulfilment_service.DISPATCH_TO_DOOR
    )


@pytest.mark.asyncio
async def test_a_packed_solo_booking_leaves_now():
    result = await _fulfilment(
        _order(status=OrderStatusEnum.PACKED), _delivery(provider="noon_send")
    )
    assert result.precision == "time"
    assert (
        result.estimated_at == NOW.astimezone(TZ) + fulfilment_service.DISPATCH_TO_DOOR
    )


@pytest.mark.asyncio
async def test_the_estimate_sharpens_the_moment_a_rider_is_holding_it():
    """
    The whole point of the out-for-delivery email. Before pickup the wait is a
    kitchen and a run schedule; after it, one person driving one route — so the
    estimate is measured from an event the courier reported rather than from
    anything assumed at checkout.
    """
    picked_up = NOW - timedelta(minutes=10)
    result = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(courier_status="picked_up", picked_up_at=picked_up),
    )
    assert result.precision == "time"
    assert (
        result.estimated_at
        == picked_up.astimezone(TZ) + fulfilment_service.RIDER_TO_DOOR
    )


@pytest.mark.asyncio
async def test_out_for_delivery_by_hand_still_only_promises_the_day():
    """
    A third-party order marked out for delivery was marked by a person, not by a
    courier. There is no pickup time to measure from and no van we can see, so
    the day is all there is.
    """
    result = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(provider="third_party"),
    )
    assert result.precision == "day"


@pytest.mark.asyncio
async def test_a_settled_order_promises_nothing():
    result = await _fulfilment(_order(status=OrderStatusEnum.CANCELLED))
    assert result.estimated_at is None
    assert result.precision is None


@pytest.mark.asyncio
async def test_a_delivered_order_reports_the_real_moment_not_an_estimate():
    delivered = NOW - timedelta(minutes=20)
    result = await _fulfilment(
        _order(status=OrderStatusEnum.DELIVERED),
        _delivery(courier_status="completed", delivered_at=delivered),
    )
    assert result.precision == "exact"
    assert result.estimated_at == delivered.astimezone(TZ)


# ── what the customer is told, and what they are not ──────────────────────────


@pytest.mark.asyncio
async def test_the_live_map_is_withheld_until_a_rider_actually_has_the_parcel():
    """
    A share link for a booking nobody has collected renders an empty map, which
    reads as a link that does not work.
    """
    booked = _delivery(courier_status="assigning_driver", share_link="https://share/x")
    assert (
        await _fulfilment(_order(status=OrderStatusEnum.PACKED), booked)
    ).tracking_url is None

    moving = _delivery(courier_status="picked_up", share_link="https://share/x")
    assert (
        await _fulfilment(_order(status=OrderStatusEnum.OUT_FOR_DELIVERY), moving)
    ).tracking_url == "https://share/x"


@pytest.mark.asyncio
async def test_a_third_party_order_never_gets_a_tracking_link():
    result = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(provider="third_party", share_link="https://share/x"),
    )
    assert result.tracking_url is None


@pytest.mark.asyncio
async def test_nothing_in_the_customer_view_names_the_courier():
    """
    The rule the whole module exists to keep. `OrderDeliveryResponse` carries
    the provider, the driver and the cost; this one has nowhere to put them, and
    a field added by accident would show up right here.
    """
    result = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(
            provider="noon_send",
            courier_status="picked_up",
            share_link="https://share/x",
        ),
    )
    fields = set(vars(result))
    assert not fields & {
        "provider",
        "courier_status",
        "driver_name",
        "driver_phone",
        "driver_plate",
        "cost_total",
        "zone_name",
    }
    # `courier_managed` is a boolean about the *consequence* — that we will hear
    # when a rider collects — and deliberately not the brand.
    assert result.courier_managed is True


@pytest.mark.asyncio
async def test_a_collection_order_carries_its_branch_and_a_delivery_does_not():
    branch = Branch(
        name="Melting Moments Cakes",
        reference="K001",
        address="Garden Tower 1, Al Majaz 3, Sharjah",
        city="Sharjah",
        latitude=25.3304139,
        longitude=55.3736131,
    )
    collected = await _fulfilment(
        _order(
            status=OrderStatusEnum.CONFIRMED,
            method=DeliveryMethodEnum.PICKUP,
            branch_id=uuid.uuid4(),
        ),
        branch=branch,
    )
    assert collected.branch is branch

    delivered = await _fulfilment(
        _order(status=OrderStatusEnum.CONFIRMED, branch_id=uuid.uuid4()),
        _delivery(),
        branch=branch,
    )
    # Which kitchen baked it is not a customer's business, and an address they
    # should not drive to would be worse than none.
    assert delivered.branch is None


# ── the branch itself ─────────────────────────────────────────────────────────


def test_a_branch_maps_link_uses_its_pin_rather_than_its_address_text():
    """
    A search by address text lands on whatever Google decides that string means,
    which for a shop inside a tower is regularly the tower's other entrance.
    """
    branch = Branch(
        name="x", reference="K001", latitude=25.3304139, longitude=55.3736131
    )
    assert branch.maps_url == (
        "https://www.google.com/maps/search/?api=1&query=25.3304139,55.3736131"
    )
    assert Branch(name="x", reference="B002").maps_url is None


def test_branch_localisation_reads_the_nested_translations_shape():
    """
    `translations` is `{locale: {field: value}}`, like every other translatable
    model. It used to be indexed one level deep and the result treated as a
    string, so it returned nothing for every branch that had translations.
    """
    branch = Branch(
        name="Melting Moments Cakes",
        name_localized="ملتينج مومنتس",
        reference="K001",
        address="Garden Tower 1, Al Majaz 3",
        city="Sharjah",
        city_localized="الشارقة",
        translations={"ar": {"name": "ملتينج مومنتس كيكس", "address": "جاردن تاور 1"}},
    )
    assert branch.name_for("en") == "Melting Moments Cakes"
    assert branch.name_for("ar") == "ملتينج مومنتس كيكس"
    assert branch.address_for("ar") == "جاردن تاور 1"
    # Falls back to the `*_localized` column when `translations` has no entry,
    # which is how the data actually arrives from the admin form.
    assert branch.city_for("ar") == "الشارقة"
    # And to English when there is nothing Arabic at all, rather than to blank.
    assert (
        Branch(name="Only English", reference="B003").name_for("ar") == "Only English"
    )
