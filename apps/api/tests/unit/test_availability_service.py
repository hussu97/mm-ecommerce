"""
What a branch can make, and when it can make it again.

The rules here decide whether a customer is offered a cake, so each of these is
a sale that either happens or does not. The two that matter most are the ones
that are easy to get backwards: stock returns *on read* rather than when a job
runs, and a product is hidden from the catalogue only when every branch is out
— never when one is.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import availability_service as availability
from app.services import business_day_service


def _option(name: str, *, active: bool = True) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name, is_active=active)


def _group(name: str, options: list, *, minimum: int) -> SimpleNamespace:
    modifier = SimpleNamespace(
        id=uuid.uuid4(), name=name, is_active=True, options=options
    )
    return SimpleNamespace(modifier=modifier, minimum_options=minimum, display_order=0)


def _product(name: str = "Box of 3", groups: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name, product_modifiers=groups or [])


class TestStockComesBackOnItsOwn:
    """
    Nothing has to run for a shop to start selling again.

    The counter marks kunafa out until close and goes home. If the return
    depended on a sweep, a container that failed to start would leave the
    branch unable to sell it the next morning — a silent outage of exactly the
    items somebody cared enough to mark.
    """

    async def test_indefinite_has_no_clock_at_all(self):
        # Null is the absence of a return time, not a very distant one. A row
        # with a far-future timestamp would eventually lapse on its own, which
        # is exactly what "until somebody says so" must not do.
        until = await availability.resolve_until(
            None, branch=SimpleNamespace(), duration=availability.DURATION_INDEFINITE
        )
        assert until is None

    async def test_an_hour_is_an_hour_from_now(self):
        now = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
        until = await availability.resolve_until(
            None,
            branch=SimpleNamespace(),
            duration=availability.DURATION_ONE_HOUR,
            now=now,
        )
        assert until == now + timedelta(hours=1)

    async def test_a_duration_nobody_offers_is_refused(self):
        # Better a 500 in a log than a row that silently never comes back.
        with pytest.raises(ValueError):
            await availability.resolve_until(
                None, branch=SimpleNamespace(), duration="next_tuesday"
            )

    def test_the_terminal_and_the_service_offer_the_same_three(self):
        assert availability.DURATIONS == (
            availability.DURATION_INDEFINITE,
            availability.DURATION_END_OF_DAY,
            availability.DURATION_ONE_HOUR,
        )


class TestWhenTheDayEnds:
    """
    "Until end of the day of operations" is the branch's day, not midnight.

    A shop trading past midnight on a 04:00 cut-off is still working the same
    day. Marking something out at 01:00 and having it return at 00:00 — an hour
    *earlier* than it was marked, or an hour later depending on how you count —
    is the bug this exists to prevent.
    """

    def _branch(self, cutoff: str = "04:00"):
        return SimpleNamespace(business_day_start=cutoff)

    def test_after_the_cutoff_the_day_ends_tomorrow_morning(self):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Dubai")
        # 6pm Dubai: the day that began at 4am this morning ends at 4am tomorrow.
        moment = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)  # 18:00 +04
        rollover = business_day_service.next_rollover(self._branch(), moment, tz)
        assert rollover.astimezone(tz).hour == 4
        assert rollover.astimezone(tz).day == 16

    def test_before_the_cutoff_the_day_ends_this_morning(self):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Dubai")
        # 01:00 Dubai is still yesterday's trading day, which ends in 3 hours.
        moment = datetime(2026, 8, 15, 21, 0, tzinfo=timezone.utc)  # 01:00 +04 on 16th
        rollover = business_day_service.next_rollover(self._branch(), moment, tz)
        local = rollover.astimezone(tz)
        assert local.hour == 4 and local.day == 16
        assert rollover - moment == timedelta(hours=3)

    def test_an_unparseable_cutoff_falls_back_to_four(self):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Dubai")
        moment = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
        rollover = business_day_service.next_rollover(
            self._branch("nonsense"), moment, tz
        )
        assert rollover.astimezone(tz).hour == 4


class TestARequiredGroupWithNothingLeft:
    """
    A box of three is unsellable when every filling is gone, and perfectly
    sellable when one remains. Getting this backwards either sells a box that
    cannot be filled or hides one that can.
    """

    def test_one_surviving_option_keeps_the_product_sellable(self):
        fudge, ferrero = _option("Fudge"), _option("Ferrero")
        product = _product(groups=[_group("Fillings", [fudge, ferrero], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(),
            unavailable_option_ids=frozenset({fudge.id}),
        )
        assert state.product_available(product)
        assert state.blocking_groups(product) == []

    def test_a_required_group_with_every_option_out_blocks_the_product(self):
        fudge, ferrero = _option("Fudge"), _option("Ferrero")
        product = _product(groups=[_group("Fillings", [fudge, ferrero], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(),
            unavailable_option_ids=frozenset({fudge.id, ferrero.id}),
        )
        assert not state.product_available(product)
        assert [g.modifier.name for g in state.blocking_groups(product)] == ["Fillings"]

    def test_an_optional_group_with_nothing_left_blocks_nothing(self):
        # Nobody had to choose from it, so a cake with no candles left is a
        # cake, not an outage.
        candle = _option("Candle")
        product = _product(groups=[_group("Candles", [candle], minimum=0)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(), unavailable_option_ids=frozenset({candle.id})
        )
        assert state.product_available(product)

    def test_a_deactivated_option_does_not_keep_a_group_alive(self):
        # A de-listed option is not something the customer can pick, so a group
        # whose only survivor is inactive has nothing left in it.
        live, retired = _option("Fudge"), _option("Discontinued", active=False)
        product = _product(groups=[_group("Fillings", [live, retired], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(), unavailable_option_ids=frozenset({live.id})
        )
        assert not state.product_available(product)

    def test_a_product_marked_out_is_out_whatever_its_options_say(self):
        fudge = _option("Fudge")
        product = _product(groups=[_group("Fillings", [fudge], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(),
            unavailable_product_ids=frozenset({product.id}),
        )
        assert not state.product_available(product)


class TestWhatTheCustomerIsTold:
    """
    The sentence beside a blocked line, because "unavailable" is not something
    anybody can act on and "no fillings left today" is.
    """

    def test_a_chosen_option_running_out_names_that_option(self):
        fudge = _option("Fudge")
        product = _product(groups=[_group("Fillings", [fudge], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(), unavailable_option_ids=frozenset({fudge.id})
        )
        line = availability.line_unavailability(
            state,
            product=product,
            # The website dialect: `option_id` / `option_name`.
            selected_options=[{"option_id": str(fudge.id), "option_name": "Fudge"}],
        )
        assert line is not None
        assert line.unavailable_option_names == ("Fudge",)
        assert "Fudge" in line.reason

    def test_the_counter_dialect_is_read_too(self):
        # `pos_order_service` writes `modifier_option_id` / `name`. One reader
        # has to understand both or a counter-written basket is never checked.
        fudge = _option("Fudge")
        product = _product(groups=[_group("Fillings", [fudge], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(), unavailable_option_ids=frozenset({fudge.id})
        )
        line = availability.line_unavailability(
            state,
            product=product,
            selected_options=[{"modifier_option_id": str(fudge.id), "name": "Fudge"}],
        )
        assert line is not None and line.unavailable_option_names == ("Fudge",)

    def test_an_option_the_customer_did_not_choose_is_irrelevant(self):
        chosen, other = _option("Fudge"), _option("Ferrero")
        product = _product(groups=[_group("Fillings", [chosen, other], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(), unavailable_option_ids=frozenset({other.id})
        )
        line = availability.line_unavailability(
            state,
            product=product,
            selected_options=[{"option_id": str(chosen.id), "option_name": "Fudge"}],
        )
        assert line is None

    def test_an_unreadable_historic_id_does_not_block_a_sale(self):
        # Snapshots predating UUID ids exist. Nothing can be looked up for them,
        # and refusing the basket would be blocking a sale to protect a check
        # that cannot run.
        product = _product(groups=[])
        state = availability.BranchAvailability(branch_id=uuid.uuid4())
        line = availability.line_unavailability(
            state,
            product=product,
            selected_options=[{"option_id": "legacy-7", "option_name": "Fudge"}],
        )
        assert line is None

    def test_a_product_with_nothing_wrong_produces_no_line(self):
        product = _product(groups=[])
        state = availability.BranchAvailability(branch_id=uuid.uuid4())
        assert (
            availability.line_unavailability(
                state, product=product, selected_options=None
            )
            is None
        )

    def test_a_blocked_group_says_what_ran_out(self):
        fudge = _option("Fudge")
        product = _product(groups=[_group("Fillings", [fudge], minimum=1)])
        state = availability.BranchAvailability(
            branch_id=uuid.uuid4(), unavailable_option_ids=frozenset({fudge.id})
        )
        # Nothing chosen yet — the block is the group, not the choice.
        line = availability.line_unavailability(
            state, product=product, selected_options=[]
        )
        assert line is not None
        assert line.empty_group_names == ("Fillings",)
        assert "fillings" in line.reason.lower()


class TestWithoutABranchThereIsNothingToSay:
    """
    The catalogue has no address. A shopper browsing has named no branch, and
    the per-branch layer must stay silent rather than guess at one — guessing
    is how a cake gets hidden from somebody whose own shop has it.
    """

    async def test_no_branch_means_no_restrictions(self):
        state = await availability.load_for_branch(None, None)
        assert not state.unavailable_product_ids
        assert not state.unavailable_option_ids

    async def test_no_branch_blocks_no_cart_line(self):
        blocked = await availability.unavailable_cart_lines(
            None, cart=SimpleNamespace(items=[]), branch_id=None
        )
        assert blocked == []
