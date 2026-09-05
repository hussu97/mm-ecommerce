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
from app.services.delivery import fulfilment_service
from app.services.delivery.fulfilment_service import TZ

#: A Tuesday lunchtime in Sharjah, so nothing under test straddles midnight.
NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)


def _order(
    *,
    status: OrderStatusEnum,
    method: DeliveryMethodEnum = DeliveryMethodEnum.DELIVERY,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    branch_id: uuid.UUID | None = None,
    promised_at: datetime | None = None,
    promised_precision: str | None = None,
) -> SimpleNamespace:
    # `promised_*` default to None, which is an order written before checkout
    # started recording what it said — the case every test here was originally
    # written against. Tests that care about the promise pass one.
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        delivery_method=method,
        created_at=created_at or NOW - timedelta(minutes=30),
        updated_at=updated_at or NOW,
        branch_id=branch_id,
        promised_at=promised_at,
        promised_precision=promised_precision,
    )


def _delivery(**overrides) -> SimpleNamespace:
    """A stand-in for the `order_deliveries` row.

    `picked_up_at` and `delivered_at` used to live here and no longer do — they
    were a second copy of two moments `order_status_events` records, and the
    history is the source now. Tests that need them pass `reached=`.

    Every field the code reads is set, including `original_provider`, which is
    null on all but a reassigned order: a `SimpleNamespace` standing in for an
    ORM row answers only for the attributes it was given, so an unset one is an
    `AttributeError` rather than the `None` the column would have returned.
    """
    base = dict(
        provider="lalamove",
        original_provider=None,
        courier_status=None,
        share_link=None,
        dispatchable_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Db:
    """
    A session answering the three questions `for_order` asks: the delivery, the
    branch, and the courier row behind the promise.

    The courier answers `None` by default, which the estimate reads as "no
    configured promise" and falls back to the flat rider figure — so these tests
    stay about the stage machine rather than about the minutes.
    """

    def __init__(self, delivery=None, branch=None, courier=None):
        self._delivery = delivery
        self._branch = branch
        self._courier = courier

    async def execute(self, _stmt):
        delivery = self._delivery
        courier = self._courier

        class _Scalars:
            def first(self_inner):
                return delivery

            def all(self_inner):
                # The branch's weekly schedule query (for the pickup window)
                # resolves to "no schedule" here — these tests are about the
                # stage machine and the branch identity, not the hours.
                return []

        class _Result:
            def scalars(self_inner):
                return _Scalars()

            def scalar_one_or_none(self_inner):
                return courier

        return _Result()

    async def get(self, _model, _pk):
        return self._branch


async def _fulfilment(order, delivery=None, branch=None, reached=None):
    """*reached* is the order's status history — `{status: moment}`.

    Passed explicitly rather than queried, because `_Db` is not a database.
    It is what `packed_at`, `picked_up_at` and `delivered_at` are read from
    now, so a test about a collected parcel puts the moment here.
    """
    return await fulfilment_service.for_order(
        _Db(delivery, branch), order, now=NOW, reached=reached or {}
    )


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
        # A failed handover reads off the status now, not only off the courier
        # record — the two used to disagree, and the status was the wrong one.
        (OrderStatusEnum.UNDELIVERED, DeliveryMethodEnum.DELIVERY, "undelivered"),
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
    assert result.precision == "day_by"
    assert result.estimated_at.date() == (NOW + timedelta(days=1)).astimezone(TZ).date()


@pytest.mark.asyncio
async def test_an_order_with_no_delivery_record_at_all_is_treated_as_third_party():
    """A delivery placed before the row existed still has to answer the question."""
    result = await _fulfilment(_order(status=OrderStatusEnum.CONFIRMED), None)
    assert result.precision == "day_by"
    assert result.courier_managed is False


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
        _delivery(courier_status="picked_up"),
        reached={"out_for_delivery": picked_up},
    )
    assert result.precision == "time"
    assert result.estimated_at == picked_up.astimezone(TZ) + (
        fulfilment_service.RIDER_TO_DOOR - fulfilment_service.COLLECTION_ALLOWANCE
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
    assert result.precision == "day_by"


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
        _delivery(courier_status="completed"),
        reached={"delivered": delivered},
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


# ── who hands over the tracking link ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_courier_that_texts_the_customer_is_flagged_once_it_moves():
    """
    noon Send send the customer their own tracking link by SMS after pickup and
    publish nothing to us, so there is no share link to show. The flag is what
    lets a renderer say "check your messages" rather than showing nothing.
    """
    fulfilment = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(provider="noon_send", courier_status="picked_up"),
        reached={"out_for_delivery": NOW},
    )
    assert fulfilment.tracking_by_sms is True
    assert fulfilment.tracking_url is None


@pytest.mark.asyncio
async def test_nothing_is_promised_before_a_rider_has_the_parcel():
    """The message goes out on pickup. Promising it earlier is an empty inbox."""
    fulfilment = await _fulfilment(
        _order(status=OrderStatusEnum.PACKED),
        _delivery(provider="noon_send", courier_status="assigned"),
    )
    assert fulfilment.tracking_by_sms is False


@pytest.mark.asyncio
async def test_a_courier_that_gives_us_a_link_is_not_flagged():
    """Lalamove hand us a share link, so we show it ourselves."""
    fulfilment = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(
            provider="lalamove",
            courier_status="PICKED_UP",
            picked_up_at=NOW,
            share_link="https://track.example/abc",
        ),
    )
    assert fulfilment.tracking_by_sms is False
    assert fulfilment.tracking_url == "https://track.example/abc"


@pytest.mark.asyncio
async def test_a_third_party_order_is_never_flagged():
    fulfilment = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(provider="third_party"),
    )
    assert fulfilment.tracking_by_sms is False


# ── the promise checkout made ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_confirmed_order_repeats_what_the_checkout_said():
    """
    The bug this whole column exists for.

    MM-20260805-008 was placed at 13:45 into a batch window closing at 18:00.
    Checkout promised 19:00. The confirmation email said 17:25, because a batch
    is only assigned at PACKED and nothing at CONFIRMED could see the window —
    so it fell through to a generic `created_at + 2h prep + 1h drive`.

    Both numbers were computed correctly. They were answering different
    questions, and only one of them had been said out loud to a customer.
    """
    promised = NOW + timedelta(hours=5, minutes=15)

    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.CONFIRMED,
            created_at=NOW - timedelta(minutes=10),
            promised_at=promised,
            promised_precision="time",
        ),
        _delivery(),
    )

    assert result.estimated_at == promised.astimezone(TZ)
    assert result.precision == "time"
    # The number it used to give, so a regression is unmistakable rather than
    # merely a different hour.
    generic = (
        (NOW - timedelta(minutes=10)).astimezone(TZ)
        + fulfilment_service.KITCHEN_PREP
        + fulfilment_service.DISPATCH_TO_DOOR
    )
    assert result.estimated_at != generic


@pytest.mark.asyncio
async def test_a_third_party_day_promise_gains_a_bound_but_not_a_date():
    """
    A third-party zone is quoted to the day at checkout, and repeating it as an
    hour would borrow a precision that belongs to somebody else's van.

    It does gain a *bound*. "Tuesday" is a date; "Tuesday before 10 PM" is
    something a customer can plan around, and it commits us to nothing the
    partner has not already agreed to — they finish at ten. The **date never
    moves**, which is the part that matters: this sharpens what the customer was
    told, it does not replace it.
    """
    promised = NOW + timedelta(days=1)

    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.CONFIRMED,
            promised_at=promised,
            promised_precision="day",
        ),
        _delivery(provider="third_party"),
    )

    assert result.precision == "day_by"
    # Same day they were promised at checkout, now bounded at the partner's
    # closing hour.
    assert result.estimated_at.date() == promised.astimezone(TZ).date()
    assert result.estimated_at.hour == fulfilment_service.THIRD_PARTY_BY_HOUR


@pytest.mark.asyncio
async def test_a_rider_holding_the_parcel_beats_the_promise():
    """
    The one thing allowed to overrule what was said: an event that actually
    happened. One rider, one route, measured from a stamp the courier reported.
    """
    picked_up = NOW - timedelta(minutes=5)

    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.OUT_FOR_DELIVERY,
            promised_at=NOW + timedelta(hours=6),
            promised_precision="time",
        ),
        _delivery(),
        reached={"out_for_delivery": picked_up},
    )

    assert result.estimated_at == picked_up.astimezone(TZ) + (
        fulfilment_service.RIDER_TO_DOOR - fulfilment_service.COLLECTION_ALLOWANCE
    )


@pytest.mark.asyncio
async def test_a_promise_already_past_is_still_the_promise():
    """
    A late order is late, and the tracking page should say so.

    Replacing an overdue promise with a freshly-derived one would quietly move
    the goalposts every time the page was refreshed, which is the one thing a
    customer waiting past their time would notice and not forgive.
    """
    promised = NOW - timedelta(hours=2)

    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.CONFIRMED,
            promised_at=promised,
            promised_precision="time",
        ),
        _delivery(),
    )

    assert result.estimated_at == promised.astimezone(TZ)


@pytest.mark.asyncio
async def test_an_order_placed_before_the_column_existed_still_answers():
    """No promise recorded is not a reason to say nothing — it is a reason to
    derive one, which is what every order used to get."""
    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.CONFIRMED, created_at=NOW - timedelta(minutes=30)
        ),
        _delivery(),
    )

    assert result.precision == "time"
    assert result.estimated_at == (
        (NOW - timedelta(minutes=30)).astimezone(TZ)
        + fulfilment_service.KITCHEN_PREP
        + fulfilment_service.DISPATCH_TO_DOOR
    )


# ── a promise survives a change of courier ────────────────────────────────────
#
# An admin can move a packed third-party order onto Lalamove. Every branch of
# `_estimate` used to key its sharpness off `provider in _BOOKED_BY_US`, so the
# moment the column flipped, an order promised a *date* started being answered
# with an *hour* — a precision nobody had offered and that belongs to a
# schedule the customer was never quoted.
#
# `tasks/lessons.md`, 2026-08-05: a promise is a fact about what was said, not a
# calculation to repeat. What was said is `promised_precision`, and it is a
# ceiling for the life of the order.


@pytest.mark.asyncio
async def test_a_day_promise_survives_the_order_moving_to_a_booked_courier():
    """Still in the kitchen, now on Lalamove. Still a date."""
    promised = NOW + timedelta(days=1)
    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.PACKED,
            promised_at=promised,
            promised_precision="day",
        ),
        _delivery(provider="lalamove", original_provider="third_party"),
    )
    assert result.precision == "day_by"
    assert result.estimated_at.date() == promised.astimezone(TZ).date()
    assert result.estimated_at.hour == fulfilment_service.THIRD_PARTY_BY_HOUR


@pytest.mark.asyncio
async def test_a_rider_collecting_does_not_sharpen_a_day_promise():
    """
    The sharpest case in the whole function, and the one that would have broken
    it: a real pickup event on a courier we book returns an hour. It may only
    do that for an order that was promised an hour.
    """
    promised = NOW + timedelta(days=1)
    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.OUT_FOR_DELIVERY,
            promised_at=promised,
            promised_precision="day",
        ),
        _delivery(provider="lalamove", original_provider="third_party"),
        reached={"out_for_delivery": NOW - timedelta(minutes=5)},
    )
    assert result.precision == "day_by", "a day promise must never become an hour"
    # And it is the promised day, not today. A rider collecting early does not
    # move the date the customer was given.
    assert result.estimated_at.date() == promised.astimezone(TZ).date()


@pytest.mark.asyncio
async def test_a_time_promise_still_sharpens_as_it_always_did():
    """The pinning must not flatten the orders that were promised an hour."""
    result = await _fulfilment(
        _order(
            status=OrderStatusEnum.OUT_FOR_DELIVERY,
            promised_at=NOW + timedelta(hours=6),
            promised_precision="time",
        ),
        _delivery(provider="lalamove"),
        reached={"out_for_delivery": NOW - timedelta(minutes=5)},
    )
    assert result.precision == "time"


@pytest.mark.asyncio
async def test_a_reassigned_order_with_no_promise_keeps_the_day_shape():
    """
    An order written before `promised_at` existed has nothing stored to repeat,
    and the fallback used to read the *current* provider — so reassigning one
    would turn "tomorrow before 10 PM" into an hour. `original_provider` is what
    remembers that this was somebody else's van.
    """
    result = await _fulfilment(
        _order(status=OrderStatusEnum.PACKED),
        _delivery(provider="lalamove", original_provider="third_party"),
    )
    assert result.precision == "day_by"


@pytest.mark.asyncio
async def test_the_timeline_stamps_come_from_the_history():
    """
    They used to be three columns on `order_deliveries` that only an integrated
    courier ever filled, which is why a third-party order showed one stamp and
    four blanks.
    """
    packed = NOW - timedelta(hours=2)
    collected = NOW - timedelta(hours=1)
    result = await _fulfilment(
        _order(status=OrderStatusEnum.OUT_FOR_DELIVERY),
        _delivery(provider="third_party"),
        reached={"packed": packed, "out_for_delivery": collected},
    )
    assert result.packed_at == packed
    assert result.picked_up_at == collected
    assert result.delivered_at is None
