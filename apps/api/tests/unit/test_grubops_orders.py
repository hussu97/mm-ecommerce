"""The order-ingest decisions, tested where they can be tested without a DB.

The create path itself writes rows and is exercised against a real Postgres in
development; what lives here is the logic that decides *what* to write and *when*
to call GrubOps — the status mapping, the positional line grouping, the
lifecycle ladder, the write-back gate, and the loop's change detector.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.order import OrderStatusEnum
from app.services.grubops import grubops_orders as loop
from app.services.grubops import grubops_orders_service as g


def test_every_live_status_maps_to_a_lifecycle_state_except_on_hold():
    # The loop asks GrubOps for these; each must resolve to an MM status, or an
    # order sits in a state the ingest silently cannot represent. On-hold is the
    # one deliberate omission — a hold is not a move of ours.
    for status in loop.grubops_orders_service.LIVE_STATUSES:
        assert status in g._STATUS_TO_MM
    assert "OrderOnHold" not in g._STATUS_TO_MM


def test_a_completed_order_means_delivered_and_a_rejected_one_cancelled():
    assert g._STATUS_TO_MM["OrderCompleted"] == OrderStatusEnum.DELIVERED
    assert g._STATUS_TO_MM["OrderRejected"] == OrderStatusEnum.CANCELLED


def test_a_prepared_order_stops_at_the_shop_not_packed():
    # The shop, not the poll loop, owns `packed`: it is the Packed button that
    # fires GrubOps force-complete and calls the rider. So the prepared/dispatched
    # family maps to `arrived_at_pos` and waits there — mirroring it straight to
    # `packed` would jump past the shop and skip the force-complete we now owe.
    assert g._STATUS_TO_MM["OrderAccepted"] == OrderStatusEnum.ARRIVED_AT_POS
    assert g._STATUS_TO_MM["OrderPrepared"] == OrderStatusEnum.ARRIVED_AT_POS
    assert g._STATUS_TO_MM["OrderDispatched"] == OrderStatusEnum.ARRIVED_AT_POS


def test_modifiers_attach_to_the_item_above_them():
    lines = [
        {"type": "ITEM", "name": "Box A"},
        {"type": "MODIFIER", "name": "a1"},
        {"type": "MODIFIER", "name": "a2"},
        {"type": "ITEM", "name": "Box B"},
        {"type": "MODIFIER", "name": "b1"},
    ]
    groups = g._group_lines(lines)
    assert [gp["item"]["name"] for gp in groups] == ["Box A", "Box B"]
    assert [m["name"] for m in groups[0]["modifiers"]] == ["a1", "a2"]
    assert [m["name"] for m in groups[1]["modifiers"]] == ["b1"]


def test_a_leading_modifier_with_no_item_is_dropped_not_crashed():
    # A malformed payload must not take the whole order down.
    assert g._group_lines([{"type": "MODIFIER", "name": "orphan"}]) == []


def test_a_null_subtotal_is_read_as_zero_not_a_crash():
    # Cash and some prepaid orders send `subtotal: null`; the ingest leans on
    # unitPrice instead, and `_num` must never raise on the null.
    assert g._num(None) == Decimal("0")
    assert g._num("40.0") == Decimal("40.0")
    assert g._num("not a number") == Decimal("0")


@pytest.mark.asyncio
async def test_the_ladder_climbs_created_to_delivered_one_rung_at_a_time():
    order = SimpleNamespace(
        status=OrderStatusEnum.CREATED, pos_status="pending", closed_at=None
    )
    moved: list[OrderStatusEnum] = []

    async def fake_transition(db, o, new_status, *, on_invalid="raise"):
        moved.append(new_status)
        o.status = new_status
        return True

    # A first-seen already-completed order still backfills honestly, one rung at
    # a time, through `arrived_at_pos` and `packed` on its way to `delivered`.
    with patch.object(g.order_lifecycle, "transition", new=fake_transition):
        await g._apply_status(None, order, OrderStatusEnum.DELIVERED, [])

    assert moved == [
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.ARRIVED_AT_POS,
        OrderStatusEnum.PACKED,
        OrderStatusEnum.DELIVERED,
    ]
    # Delivered closes the check on the board.
    assert order.pos_status == g.PosOrderStatusEnum.CLOSED.value
    # ...and stamps when it closed, because no cashier is here to. A closed
    # check with a null closed_at is what left the reports blank and is what the
    # constraint added alongside this now forbids.
    assert order.closed_at is not None


@pytest.mark.asyncio
async def test_a_live_order_climbs_only_to_the_shop_and_waits():
    # The ordinary live case: GrubOps reports the order prepared, which now
    # targets `arrived_at_pos`. The ladder stops there — it must not walk on to
    # `packed`, which is the shop's to press.
    order = SimpleNamespace(status=OrderStatusEnum.CREATED, pos_status="pending")
    moved: list[OrderStatusEnum] = []

    async def fake_transition(db, o, new_status, *, on_invalid="raise"):
        moved.append(new_status)
        o.status = new_status
        return True

    with patch.object(g.order_lifecycle, "transition", new=fake_transition):
        await g._apply_status(None, order, OrderStatusEnum.ARRIVED_AT_POS, [])

    assert moved == [OrderStatusEnum.CONFIRMED, OrderStatusEnum.ARRIVED_AT_POS]
    assert OrderStatusEnum.PACKED not in moved


@pytest.mark.asyncio
async def test_a_cancel_is_attempted_directly_rather_than_climbed():
    order = SimpleNamespace(
        status=OrderStatusEnum.CONFIRMED, pos_status="active", closed_at=None
    )
    seen: list[OrderStatusEnum] = []

    async def fake_transition(db, o, new_status, *, on_invalid="raise"):
        seen.append(new_status)
        o.status = new_status
        return True

    with patch.object(g.order_lifecycle, "transition", new=fake_transition):
        await g._apply_status(None, order, OrderStatusEnum.CANCELLED, [])

    assert seen == [OrderStatusEnum.CANCELLED]
    assert order.pos_status == g.PosOrderStatusEnum.VOID.value


@pytest.mark.asyncio
async def test_write_back_skips_and_records_when_grubops_forbids_it():
    order_map = SimpleNamespace(
        grubops_order_id="123", last_pushed_status=None, last_push_error=None
    )
    db = _fake_db(order_map)
    fake_provider = SimpleNamespace(
        get_order=AsyncMock(
            return_value={"orderManagementOptions": {"forceCancellationAllowed": False}}
        ),
        force_cancel=AsyncMock(),
    )
    with patch.object(g, "provider", fake_provider):
        await g.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.CANCELLED, actor="admin"
        )
    fake_provider.force_cancel.assert_not_awaited()
    assert "forceCancellationAllowed" in order_map.last_push_error
    assert order_map.last_pushed_status is None


@pytest.mark.asyncio
async def test_write_back_force_cancels_when_grubops_allows_it():
    order_map = SimpleNamespace(
        grubops_order_id="123", last_pushed_status=None, last_push_error="stale"
    )
    db = _fake_db(order_map)
    fake_provider = SimpleNamespace(
        get_order=AsyncMock(
            return_value={"orderManagementOptions": {"forceCancellationAllowed": True}}
        ),
        force_cancel=AsyncMock(),
    )
    with patch.object(g, "provider", fake_provider):
        await g.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.CANCELLED, actor="admin"
        )
    fake_provider.force_cancel.assert_awaited_once()
    assert order_map.last_pushed_status == "cancelled"
    assert order_map.last_push_error is None


@pytest.mark.asyncio
async def test_write_back_force_completes_a_packed_order():
    # `packed`, not `delivered`, is what carries force-complete out — GrubOps's
    # "the order is ready, call the rider".
    order_map = SimpleNamespace(
        grubops_order_id="123", last_pushed_status=None, last_push_error="stale"
    )
    db = _fake_db(order_map)
    fake_provider = SimpleNamespace(
        get_order=AsyncMock(
            return_value={"orderManagementOptions": {"forceCompletionAllowed": True}}
        ),
        force_complete=AsyncMock(),
    )
    with patch.object(g, "provider", fake_provider):
        await g.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.PACKED, actor="pos"
        )
    fake_provider.force_complete.assert_awaited_once()
    assert order_map.last_pushed_status == "packed"
    assert order_map.last_push_error is None


@pytest.mark.asyncio
async def test_write_back_ignores_a_delivered_move():
    # Delivered has no force-* endpoint any more: force-complete fired at packed,
    # and the move to delivered is our own auto-close.
    order_map = SimpleNamespace(
        grubops_order_id="123", last_pushed_status=None, last_push_error=None
    )
    db = _fake_db(order_map)
    fake_provider = SimpleNamespace(get_order=AsyncMock(), force_complete=AsyncMock())
    with patch.object(g, "provider", fake_provider):
        await g.mirror_status_out(
            db, mm_order_id="mm1", new_status=OrderStatusEnum.DELIVERED, actor="system"
        )
    fake_provider.get_order.assert_not_awaited()
    fake_provider.force_complete.assert_not_awaited()


# ── the retry sweep: re-firing a push the immediate mirror-out never landed ──


def _sweep_db(rows, order_map=None):
    """A session whose one result serves both the candidate query (`.all()`) and
    the map lookup `mirror_status_out` makes next (`.scalar_one_or_none()`)."""

    class _Result:
        def all(self):
            return rows

        def scalar_one_or_none(self):
            return order_map

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_a_stuck_packed_push_is_retried_and_lands_when_the_gate_opens():
    # The reported bug: the immediate push found the gate shut at pack time and
    # recorded a failure. The gate is open now, so the sweep force-completes.
    order = SimpleNamespace(id="mm1", status=OrderStatusEnum.PACKED)
    order_map = SimpleNamespace(
        grubops_order_id="123",
        last_pushed_status=None,
        last_push_error="forceCompletionAllowed is false; GrubOps already moved on",
    )
    db = _sweep_db([(order, order_map)], order_map=order_map)
    fake_provider = SimpleNamespace(
        get_order=AsyncMock(
            return_value={"orderManagementOptions": {"forceCompletionAllowed": True}}
        ),
        force_complete=AsyncMock(),
    )
    with patch.object(g, "provider", fake_provider):
        landed = await g.sweep_pending_pushouts(db)
    fake_provider.force_complete.assert_awaited_once()
    assert order_map.last_pushed_status == "packed"
    assert order_map.last_push_error is None
    assert landed == 1


@pytest.mark.asyncio
async def test_an_order_already_pushed_for_its_state_is_skipped_without_grubops():
    # The busy-board common case: a landed push must cost no GrubOps call on the
    # next tick, decided in memory from the map row.
    order = SimpleNamespace(id="mm1", status=OrderStatusEnum.PACKED)
    order_map = SimpleNamespace(
        grubops_order_id="123", last_pushed_status="packed", last_push_error=None
    )
    db = _sweep_db([(order, order_map)], order_map=order_map)
    fake_provider = SimpleNamespace(get_order=AsyncMock(), force_complete=AsyncMock())
    with patch.object(g, "provider", fake_provider):
        landed = await g.sweep_pending_pushouts(db)
    fake_provider.get_order.assert_not_awaited()
    fake_provider.force_complete.assert_not_awaited()
    assert landed == 0


@pytest.mark.asyncio
async def test_a_retry_still_forbidden_is_recorded_and_not_counted():
    # GrubOps has genuinely moved on: the gate is still shut. The sweep records
    # the outcome, pushes nothing, and reports no landing.
    order = SimpleNamespace(id="mm1", status=OrderStatusEnum.CANCELLED)
    order_map = SimpleNamespace(
        grubops_order_id="123", last_pushed_status=None, last_push_error=None
    )
    db = _sweep_db([(order, order_map)], order_map=order_map)
    fake_provider = SimpleNamespace(
        get_order=AsyncMock(
            return_value={"orderManagementOptions": {"forceCancellationAllowed": False}}
        ),
        force_cancel=AsyncMock(),
    )
    with patch.object(g, "provider", fake_provider):
        landed = await g.sweep_pending_pushouts(db)
    fake_provider.force_cancel.assert_not_awaited()
    assert "forceCancellationAllowed" in order_map.last_push_error
    assert landed == 0


@pytest.mark.asyncio
async def test_a_known_order_at_the_same_status_costs_no_detail_fetch():
    # The change detector: an order already ingested at this exact status must
    # not spend a getOrderInfo call on the busy-board common case.
    existing = SimpleNamespace(mm_order_id="mm1", last_grubops_status="OrderStarted")
    db = _fake_db(existing, execute_result=existing)
    fake_provider = SimpleNamespace(get_order=AsyncMock())
    summary = {
        "orderId": "123",
        "status": "OrderStarted",
        "source": {"channel": "Talabat"},
        "externalId": "e1",
        "locationId": "L",
    }
    with patch.object(loop, "provider", fake_provider):
        spent = await loop._ingest_one(db, summary)
    assert spent is False
    fake_provider.get_order.assert_not_awaited()


# ── a minimal fake async session ────────────────────────────────────────────


def _fake_db(scalar_one_or_none=None, execute_result=None):
    class _Result:
        def scalar_one_or_none(self):
            return scalar_one_or_none

        def scalar_one(self):
            return execute_result

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_Result())
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_the_ingest_loops_own_cancel_is_not_mirrored_back_out():
    # The order_lifecycle write-back trigger must tell its own mirroring apart
    # from a person's. A move attributed `aggregator` (the ingest) is GrubOps's
    # own state coming in — echoing it straight back would be a feedback loop.
    from app.models.order_status_event import StatusSourceEnum, acting_as
    from app.services.orders import order_lifecycle

    order = _aggregator_order()
    with patch(
        "app.services.grubops.grubops_orders_service.push_status_out_in_background"
    ) as push:
        db = _stock_db()
        with acting_as(StatusSourceEnum.AGGREGATOR):
            await order_lifecycle.transition(
                db, order, OrderStatusEnum.CANCELLED, on_invalid="skip"
            )
    push.assert_not_called()


@pytest.mark.asyncio
async def test_an_admin_cancel_of_an_aggregator_order_is_mirrored_out():
    from app.models.order_status_event import StatusSourceEnum, acting_as
    from app.services.orders import order_lifecycle

    order = _aggregator_order()
    with (
        patch(
            "app.services.grubops.grubops_orders_service.push_status_out_in_background"
        ) as push,
        patch(
            "app.services.grubops.grubops_orders_service.is_enabled", return_value=True
        ),
    ):
        db = _stock_db()
        with acting_as(StatusSourceEnum.ADMIN):
            await order_lifecycle.transition(
                db, order, OrderStatusEnum.CANCELLED, on_invalid="skip"
            )
    push.assert_called_once()
    assert push.call_args.kwargs["new_status"] == OrderStatusEnum.CANCELLED


@pytest.mark.asyncio
async def test_the_shop_marking_an_aggregator_order_packed_force_completes_it():
    # The shop's Packed press (source `pos`) is what mirrors out to force-complete.
    from app.models.order_status_event import StatusSourceEnum, acting_as
    from app.services.orders import order_lifecycle

    order = _aggregator_order()  # confirmed
    with (
        patch(
            "app.services.grubops.grubops_orders_service.push_status_out_in_background"
        ) as push,
        patch(
            "app.services.grubops.grubops_orders_service.is_enabled", return_value=True
        ),
    ):
        db = _stock_db()
        with acting_as(StatusSourceEnum.POS):
            await order_lifecycle.transition(
                db, order, OrderStatusEnum.PACKED, on_invalid="skip"
            )
    push.assert_called_once()
    assert push.call_args.kwargs["new_status"] == OrderStatusEnum.PACKED


@pytest.mark.asyncio
async def test_the_five_minute_auto_close_pushes_nothing_out():
    # `packed → delivered` from the auto-close is source `system`, not aggregator,
    # so it clears the actor gate — but delivered is not a mirrored status, so
    # nothing is pushed. force-complete already fired at packed.
    from app.models.order_status_event import StatusSourceEnum, acting_as
    from app.services.orders import order_lifecycle

    order = _aggregator_order()
    order.status = OrderStatusEnum.PACKED
    with (
        patch(
            "app.services.grubops.grubops_orders_service.push_status_out_in_background"
        ) as push,
        patch(
            "app.services.grubops.grubops_orders_service.is_enabled", return_value=True
        ),
    ):
        db = _stock_db()
        with acting_as(StatusSourceEnum.SYSTEM):
            await order_lifecycle.transition(
                db, order, OrderStatusEnum.DELIVERED, on_invalid="skip"
            )
    push.assert_not_called()


def _aggregator_order():
    import uuid

    from app.models.pos_order import OrderSourceEnum

    return SimpleNamespace(
        id=uuid.uuid4(),
        status=OrderStatusEnum.CONFIRMED,
        source=OrderSourceEnum.AGGREGATOR.value,
        pos_status="active",
        items=[],
        refunded_amount=0,
        order_number="AGG-1",
    )


def _stock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    db.flush = AsyncMock()
    return db


# ── the driver-facing pickup code (audit: store both ids, print the short one) ──


def test_talabat_short_code_is_pulled_from_the_instructions():
    header = {"instructions": "No cutlery.  | Talabat-short code: 1445"}
    assert g._driver_code(header, "3841201369", {}) == "1445"


def test_a_short_numeric_external_id_is_the_code_itself():
    # Noon and Deliveroo: the "external id" already is the four-digit number the
    # customer and rider see.
    assert g._driver_code({}, "5717", {}) == "5717"
    assert g._driver_code({}, "0037", {}) == "0037"


def test_a_long_external_id_falls_back_to_the_grubops_sequence():
    # Keeta/Careem carry a long machine id and no short code; the GrubOps
    # sequence is what the console shows the counter.
    info = {"orderSequenceNumber": {"createdSequence": "15020"}}
    assert g._driver_code({}, "4997841098410262", info) == "15020"


def test_the_driver_code_is_never_the_long_id_when_a_short_one_exists():
    # The whole point of the audit: the long external id is stored on
    # external_reference, but the *printed* code is short.
    header = {"instructions": "x | short code: 1477"}
    assert g._driver_code(header, "3843227968", {}) == "1477"


# ── the courier badge (audit: aggregators are couriers, with logos) ────────────


def test_channel_names_normalise_to_courier_codes():
    from app.services.couriers import courier_catalog as cc

    assert cc.code_for_channel("Talabat") == "talabat"
    assert cc.code_for_channel("Keeta 2.0") == "keeta"
    assert cc.code_for_channel("Noon") == "noon_food"
    assert cc.code_for_channel("Deliveroo") == "deliveroo"
    assert cc.code_for_channel("Careem") == "careem"
    assert cc.code_for_channel("Something else") is None


def test_noon_food_and_noon_send_are_different_badges():
    # The marketplace and the courier are two different "noon" businesses and
    # must never share a logo.
    from app.services.couriers import courier_catalog as cc

    food = cc.badge_for_order(source="aggregator", aggregator_channel="Noon")
    send = cc.badge_for_order(source="online", delivery_provider="noon_send")
    assert food["code"] == "noon_food"
    assert send["code"] == "noon_send"
    assert food["logo_url"] != send["logo_url"]
    assert food["is_aggregator"] is True
    assert send["is_aggregator"] is False


def test_a_counter_sale_has_no_courier_badge():
    from app.services.couriers import courier_catalog as cc

    assert cc.badge_for_order(source="cashier") is None
    assert cc.badge_for_order(source="online", delivery_provider=None) is None


# ── what the customer actually asked for ──────────────────────────────────────


def test_the_note_loses_the_short_code_the_box_already_carries():
    """
    `instructions` is two things joined by a pipe, and only one of them is a note.

    The docket used to read `No cutlery.  | Talabat-short code: 1452` — with
    1452 already printed in the box at the top of the same ticket, four times
    the size. A note is the one line on a docket somebody has to act on, and
    padding it with a duplicate of the largest number on the page is how it
    stops being read.
    """
    from app.services.grubops.grubops_orders_service import _customer_note

    assert (
        _customer_note({"instructions": "No cutlery.  | Talabat-short code: 1452"})
        == "No cutlery."
    )
    assert _customer_note({"instructions": "x | short code: 1477"}) == "x"


def test_an_order_whose_note_was_only_metadata_has_no_note():
    """
    None, not an empty string.

    An empty note still prints a rule and a heading on the docket — a labelled
    blank, which reads as "something was said and we lost it".
    """
    from app.services.grubops.grubops_orders_service import _customer_note

    assert _customer_note({"instructions": "Talabat-short code: 1452"}) is None
    assert _customer_note({"instructions": ""}) is None
    assert _customer_note({}) is None


def test_a_real_note_survives_untouched():
    from app.services.grubops.grubops_orders_service import _customer_note

    assert (
        _customer_note({"instructions": "Please add candles"}) == "Please add candles"
    )


def test_the_short_code_is_still_read_from_the_raw_instructions():
    """
    Cleaning the note must not cost us the code.

    `_driver_code` reads the *raw* header, so the number still reaches the box
    where it is useful — the two functions read the same field for different
    purposes and neither may break the other.
    """
    from app.services.grubops.grubops_orders_service import _driver_code

    header = {"instructions": "No cutlery.  | Talabat-short code: 1452"}
    assert _driver_code(header, None, {}) == "1452"


# ── the aggregator rider (name / phone / status, off orderDelivery) ──────────


def test_driver_info_is_read_from_order_delivery():
    order = SimpleNamespace(
        aggregator_driver_name=None,
        aggregator_driver_phone=None,
        aggregator_driver_status=None,
    )
    info = {
        "orderDelivery": {
            "deliveryOrderDriverName": "Mohamed Sayed",
            "deliveryOrderDriverMobile": "971566189038",
            "deliveryOrderStatus": "DRIVER_ASSIGNED",
        }
    }
    g._apply_driver_info(order, info)
    assert order.aggregator_driver_name == "Mohamed Sayed"
    assert order.aggregator_driver_phone == "971566189038"
    assert order.aggregator_driver_status == "DRIVER_ASSIGNED"


def test_driver_placeholders_are_dropped_and_a_real_value_is_not_wiped():
    # GrubOps fills the fields with "UNKNOWN"/"0"/"" before a rider is assigned.
    # Those are dropped, and a real value already held is never nulled by a later
    # tick that happens to carry a placeholder.
    order = SimpleNamespace(
        aggregator_driver_name="Ali",
        aggregator_driver_phone=None,
        aggregator_driver_status=None,
    )
    info = {
        "orderDelivery": {
            "deliveryOrderDriverName": "UNKNOWN",
            "deliveryOrderDriverMobile": "0",
        }
    }
    g._apply_driver_info(order, info)
    assert order.aggregator_driver_name == "Ali"
    assert order.aggregator_driver_phone is None


# ── the customer: name / phone (+access code) / email, untangled per channel ──


def test_a_deliveroo_relay_email_is_not_shown_as_a_name():
    # Deliveroo sends the Apple private-relay address as customerName with a null
    # customerEmail. It is filed as the email and the name left blank — the row
    # must not read "5sg2…@privaterelay.appleid.com" as a name.
    name, phone, country, ptype, code, email = g._customer_fields(
        {
            "customerName": "5sg2vwy4jb@privaterelay.appleid.com",
            "customerEmail": None,
            "customerMobile": "+9718000320499",
            "customerPhoneCode": "630118286",
        }
    )
    assert name is None
    assert email == "5sg2vwy4jb@privaterelay.appleid.com"
    # The number is normalised and its access code kept apart — not joined onto it.
    assert phone == "+9718000320499"
    assert code == "630118286"
    assert (country, ptype) == ("AE", "toll_free")


def test_a_real_name_and_plain_number_pass_through():
    name, phone, country, ptype, code, email = g._customer_fields(
        {
            "customerName": "Heba Zaky",
            "customerEmail": "",
            "customerMobile": "+97144451555",
            "customerPhoneCode": None,
        }
    )
    assert name == "Heba Zaky"
    assert phone == "+97144451555"  # Talabat's masked landline, normalised
    assert (country, ptype, code, email) == ("AE", "landline", None, None)


def test_customer_placeholders_all_become_null():
    fields = g._customer_fields(
        {
            "customerName": "UNKNOWN",
            "customerEmail": None,
            "customerMobile": "UNKNOWN",
            "customerId": "UNKNOWN",
            "customerPhoneCode": None,
        }
    )
    assert fields == (None, None, None, None, None, None)


def test_customer_id_is_the_phone_fallback():
    # Some channels leave customerMobile null and carry the number on customerId.
    name, phone, country, ptype, _code, _email = g._customer_fields(
        {"customerName": "Farhat Sultana", "customerId": "+971566369787"}
    )
    assert name == "Farhat Sultana"
    assert phone == "+971566369787"
    assert (country, ptype) == ("AE", "mobile")
