"""
One modifier shape reaching the register, whichever checkout wrote it.

`order_items.selected_options_snapshot` is raw JSON and two writers fill it:
`cart_service` for the website (`option_name`, `option_price`) and
`pos_order_service` for the counter (`name`, `price`). The register knows only
the counter's, so a website order with a flavour on it failed to decode — and a
failed decode is not a degraded line, it is the whole response. A branch with
one flavoured brownie in the day's orders saw an empty queue and an empty
history.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import BadRequestError
from app.schemas.option_snapshot import OptionSnapshot
from app.services import option_snapshot


class TestForRegister:
    def test_the_counter_shape_passes_through(self):
        rows = [
            {
                "modifier_option_id": "11111111-1111-1111-1111-111111111111",
                "modifier_id": "22222222-2222-2222-2222-222222222222",
                "modifier_name": "Flavour",
                "name": "Ferrero",
                "sku": "FER",
                "price": 4.0,
                "quantity": 2,
            }
        ]

        assert option_snapshot.for_register(rows) == [
            {
                "modifier_option_id": "11111111-1111-1111-1111-111111111111",
                "modifier_id": "22222222-2222-2222-2222-222222222222",
                "modifier_name": "Flavour",
                "name": "Ferrero",
                "sku": "FER",
                "price": 4.0,
                "quantity": 2,
            }
        ]

    def test_the_website_shape_is_translated(self):
        """The exact keys `cart_service.snapshot` writes."""
        rows = [
            {
                "modifier_id": "22222222-2222-2222-2222-222222222222",
                "modifier_name": "Flavour",
                "option_id": "11111111-1111-1111-1111-111111111111",
                "modifier_option_id": "11111111-1111-1111-1111-111111111111",
                "option_name": "Fudge",
                "option_price": 3.5,
                "quantity": 3,
                "charged_total": 7.0,
            }
        ]

        assert option_snapshot.for_register(rows) == [
            {
                "modifier_option_id": "11111111-1111-1111-1111-111111111111",
                "modifier_id": "22222222-2222-2222-2222-222222222222",
                "modifier_name": "Flavour",
                "name": "Fudge",
                "sku": None,
                "price": 3.5,
                "quantity": 3,
            }
        ]

    def test_the_price_is_per_unit_not_the_charged_total(self):
        """
        `charged_total` is the line total after the group's free allowance. The
        register multiplies by quantity itself, so handing it that figure would
        count the allowance twice and print a receipt that does not add up.
        """
        rows = [
            {
                "option_name": "Fudge",
                "option_price": 3.5,
                "quantity": 3,
                "charged_total": 7.0,
            }
        ]

        assert option_snapshot.for_register(rows)[0]["price"] == 3.5

    def test_a_row_written_before_quantities_existed_counts_as_one(self):
        rows = [{"option_name": "Fudge", "option_price": 3.5}]

        assert option_snapshot.for_register(rows)[0]["quantity"] == 1

    def test_the_old_option_id_still_finds_its_id(self):
        """Web orders carried only `option_id` before `modifier_option_id`."""
        rows = [{"option_id": "abc", "option_name": "Fudge"}]

        assert option_snapshot.for_register(rows)[0]["modifier_option_id"] == "abc"

    def test_a_row_that_names_nothing_is_dropped(self):
        """
        Dropped rather than guessed at. A modifier the terminal cannot describe
        prints on a kitchen ticket, and a ticket that says the wrong thing is
        how somebody gets the wrong cake.
        """
        rows = [
            {"price": 4.0, "quantity": 1},
            {"name": "Ferrero", "price": 4.0},
            "not a dict",
            None,
        ]

        result = option_snapshot.for_register(rows)
        assert [option["name"] for option in result] == ["Ferrero"]

    def test_nothing_at_all_is_an_empty_list(self):
        assert option_snapshot.for_register(None) == []
        assert option_snapshot.for_register([]) == []
        assert option_snapshot.for_register("nonsense") == []

    def test_an_unparseable_price_is_zero_rather_than_a_crash(self):
        rows = [{"name": "Ferrero", "price": "not a number"}]

        assert option_snapshot.for_register(rows)[0]["price"] == 0.0


class TestBothDialectsNormaliseToOneShape:
    """
    The contract itself, as `schemas/option_snapshot.OptionSnapshot`.

    The column had two wire dialects and no model — `list[Any]` in the cart and
    order schemas, `list` in the POS one. One model read by both boundaries is
    what makes "these are the same row written twice" a checkable statement
    rather than a comment.
    """

    WEBSITE = {
        "modifier_id": "22222222-2222-2222-2222-222222222222",
        "modifier_name": "Flavour",
        "option_id": "11111111-1111-1111-1111-111111111111",
        "modifier_option_id": "11111111-1111-1111-1111-111111111111",
        "option_name": "Fudge",
        "option_price": 3.5,
        "quantity": 2,
        "charged_total": 3.5,
    }
    COUNTER = {
        "modifier_id": "22222222-2222-2222-2222-222222222222",
        "modifier_name": "Flavour",
        "modifier_option_id": "11111111-1111-1111-1111-111111111111",
        "name": "Fudge",
        "price": 3.5,
        "quantity": 2,
    }

    def test_the_two_dialects_land_on_the_same_row(self):
        """Not "similar": identical. Anything less is the register's bug again,
        one field further down."""
        assert (
            OptionSnapshot.model_validate(self.WEBSITE).model_dump()
            == OptionSnapshot.model_validate(self.COUNTER).model_dump()
        )

    def test_the_extra_keys_each_dialect_carries_are_not_lost_on_write(self):
        """
        `charged_total` and the translation maps are ignored by the model and
        must survive the write guard untouched — the storefront prices a basket
        off `charged_total`, so a guard that normalised on the way in would
        quietly reprice every cart.
        """
        rows = [dict(self.WEBSITE)]
        assert option_snapshot.validated(rows) == [self.WEBSITE]

    def test_a_service_may_hand_it_uuids_rather_than_strings(self):
        """Ids come out of a service as `uuid.UUID` and out of JSONB as `str`.
        Every reader of these fields wants text."""
        import uuid

        option_id = uuid.uuid4()
        row = {"name": "Fudge", "modifier_option_id": option_id}

        assert OptionSnapshot.model_validate(row).modifier_option_id == str(option_id)


class TestValidatedIsTheLoudBoundary:
    def test_a_well_formed_batch_passes_through_unchanged(self):
        rows = [{"name": "Ferrero", "price": 4.0, "quantity": 1}]

        assert option_snapshot.validated(rows) is rows

    def test_a_row_that_names_nothing_is_refused_rather_than_stored(self):
        """
        The read boundary drops such a row so one bad option cannot blank a
        branch's order queue. The write boundary must not: a row no reader can
        decode is a bug in a writer, and storing it quietly is how it is found
        six months later by the branch whose queue went blank.
        """
        with pytest.raises(BadRequestError) as raised:
            option_snapshot.validated([{"price": 4.0, "quantity": 1}])
        assert "cannot be stored" in raised.value.detail

    def test_the_message_names_the_offending_row(self):
        """A tripwire that does not say what tripped it is a 400 somebody has
        to reproduce."""
        with pytest.raises(BadRequestError) as raised:
            option_snapshot.validated([{"name": "Ferrero"}, "not a dict"])
        assert "Option 1" in raised.value.detail

    def test_nothing_at_all_is_allowed(self):
        """A line with no modifiers is the ordinary case, not a malformed one."""
        assert option_snapshot.validated([]) == []
