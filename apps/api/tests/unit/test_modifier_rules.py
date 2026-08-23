"""
One rule for what a customer may choose, and what it costs.

The register used to have none. `pos_order_service.add_item` summed the prices
the terminal sent it and never counted the picks against the group's minimum
and maximum, so a "Mix Brownies Box Of 3" could leave the till holding one
brownie or nine — and any signed-in client could ring a paid extra up at zero
by sending `price: 0`.

These tests pin the shared rule the website and both registers now go through.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError
from app.services.catalog import modifier_rules


def _option(name: str, price: str = "0", order: int = 0):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        sku=f"sku-{name.lower()}",
        price=Decimal(price),
        translations={},
        display_order=order,
        is_active=True,
    )


def _link(options, *, minimum=0, maximum=1, free=0, unique=False, name="Options"):
    return SimpleNamespace(
        id=uuid4(),
        modifier_id=uuid4(),
        minimum_options=minimum,
        maximum_options=maximum,
        free_options=free,
        unique_options=unique,
        display_order=0,
        modifier=SimpleNamespace(
            name=name, translations={}, is_active=True, options=options
        ),
    )


def _db(links):
    scalars = MagicMock()
    scalars.unique = MagicMock(return_value=links)
    result = MagicMock()
    result.scalars = MagicMock(return_value=scalars)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _product(name="Mix Brownies Box Of 3"):
    return SimpleNamespace(id=uuid4(), name=name)


# ─── The mixed box ────────────────────────────────────────────────────────────


async def test_a_box_of_three_takes_two_of_one_and_one_of_another():
    """The whole point of the product: 2 Ferrero + 1 Fudge is a box of three."""
    ferrero, fudge = _option("Ferrero"), _option("Fudge", order=1)
    link = _link([ferrero, fudge], minimum=3, maximum=3)

    resolved = await modifier_rules.resolve(
        _db([link]),
        product=_product(),
        selections=[
            modifier_rules.Selection(option_id=ferrero.id, quantity=2),
            modifier_rules.Selection(option_id=fudge.id, quantity=1),
        ],
    )

    assert {(r.option_name, r.quantity) for r in resolved} == {
        ("Ferrero", 2),
        ("Fudge", 1),
    }


async def test_the_limits_count_quantity_not_distinct_options():
    """
    Counting distinct options would let three of the same brownie through as
    "one option chosen" and reject a legitimate box as "too few".
    """
    ferrero = _option("Ferrero")
    link = _link([ferrero], minimum=3, maximum=3)

    resolved = await modifier_rules.resolve(
        _db([link]),
        product=_product(),
        selections=[modifier_rules.Selection(option_id=ferrero.id, quantity=3)],
    )

    assert resolved[0].quantity == 3


@pytest.mark.parametrize("quantity", [2, 4])
async def test_a_box_that_is_not_full_is_refused(quantity):
    ferrero = _option("Ferrero")
    link = _link([ferrero], minimum=3, maximum=3)

    with pytest.raises(BadRequestError, match="exactly 3"):
        await modifier_rules.resolve(
            _db([link]),
            product=_product(),
            selections=[
                modifier_rules.Selection(option_id=ferrero.id, quantity=quantity)
            ],
        )


async def test_repeats_and_quantities_mean_the_same_thing():
    """
    The website expresses "two Ferrero" by sending the option twice; the
    terminal sends a quantity. The same basket has to price the same either
    way, or which client rang it up would change the books.
    """
    ferrero = _option("Ferrero", "5.00")
    fudge = _option("Fudge", "5.00", order=1)
    link = _link([ferrero, fudge], minimum=3, maximum=3)

    as_repeats = await modifier_rules.resolve(
        _db([link]),
        product=_product(),
        selections=[
            modifier_rules.Selection(option_id=ferrero.id),
            modifier_rules.Selection(option_id=ferrero.id),
            modifier_rules.Selection(option_id=fudge.id),
        ],
    )
    as_quantities = await modifier_rules.resolve(
        _db([link]),
        product=_product(),
        selections=[
            modifier_rules.Selection(option_id=ferrero.id, quantity=2),
            modifier_rules.Selection(option_id=fudge.id, quantity=1),
        ],
    )

    assert [(r.option_name, r.quantity, r.charged_total) for r in as_repeats] == [
        (r.option_name, r.quantity, r.charged_total) for r in as_quantities
    ]


# ─── Prices are the catalogue's, not the client's ─────────────────────────────


async def test_the_price_comes_from_the_catalogue():
    """A terminal is a tablet on a counter. What it says a thing costs is not
    what a thing costs."""
    extra = _option("Extra shot", "7.50")
    link = _link([extra], maximum=1)

    resolved = await modifier_rules.resolve(
        _db([link]),
        product=_product("Latte"),
        selections=[modifier_rules.Selection(option_id=extra.id)],
    )

    assert resolved[0].unit_price == Decimal("7.50")
    assert resolved[0].charged_total == Decimal("7.50")


async def test_an_option_from_another_product_is_refused():
    link = _link([_option("Ferrero")], maximum=3)

    with pytest.raises(BadRequestError, match="not available"):
        await modifier_rules.resolve(
            _db([link]),
            product=_product(),
            selections=[modifier_rules.Selection(option_id=uuid4())],
        )


# ─── Free options ─────────────────────────────────────────────────────────────


async def test_free_options_are_taken_off_the_first_units_in_the_group():
    syrup = _option("Syrup", "3.00")
    link = _link([syrup], maximum=3, free=2)

    resolved = await modifier_rules.resolve(
        _db([link]),
        product=_product("Latte"),
        selections=[modifier_rules.Selection(option_id=syrup.id, quantity=3)],
    )

    # Three syrups, two of them free.
    assert resolved[0].charged_total == Decimal("3.00")


async def test_which_units_are_free_does_not_depend_on_send_order():
    """
    A basket must not price differently because the client happened to list
    its picks the other way round.
    """
    first = _option("Caramel", "3.00", order=0)
    second = _option("Vanilla", "4.00", order=1)
    link = _link([first, second], maximum=2, free=1)

    forwards = await modifier_rules.resolve(
        _db([link]),
        product=_product("Latte"),
        selections=[
            modifier_rules.Selection(option_id=first.id),
            modifier_rules.Selection(option_id=second.id),
        ],
    )
    backwards = await modifier_rules.resolve(
        _db([link]),
        product=_product("Latte"),
        selections=[
            modifier_rules.Selection(option_id=second.id),
            modifier_rules.Selection(option_id=first.id),
        ],
    )

    assert sum(r.charged_total for r in forwards) == sum(
        r.charged_total for r in backwards
    )


# ─── Unique groups ────────────────────────────────────────────────────────────


async def test_a_single_select_group_is_unique_whatever_the_flag_says():
    """
    "Size" with the flag left off would otherwise accept two larges, and the
    line would carry a size twice. The maximum catches it first and says the
    more useful thing, which is what a cashier needs to hear.
    """
    large = _option("Large", "5.00")
    link = _link([large], minimum=1, maximum=1, unique=False, name="Size")

    assert modifier_rules.effective_unique(link)
    with pytest.raises(BadRequestError, match="exactly 1 from Size"):
        await modifier_rules.resolve(
            _db([link]),
            product=_product("Latte"),
            selections=[modifier_rules.Selection(option_id=large.id, quantity=2)],
        )


async def test_a_unique_group_with_room_still_refuses_a_repeat():
    """
    Three toppings, no two the same. The quantity fits under the maximum, so
    only the unique rule can catch this one.
    """
    nuts = _option("Nuts")
    sprinkles = _option("Sprinkles", order=1)
    link = _link([nuts, sprinkles], maximum=3, unique=True, name="Toppings")

    with pytest.raises(BadRequestError, match="only choose each option once"):
        await modifier_rules.resolve(
            _db([link]),
            product=_product("Sundae"),
            selections=[modifier_rules.Selection(option_id=nuts.id, quantity=2)],
        )


async def test_a_required_group_cannot_be_skipped():
    link = _link([_option("Large")], minimum=1, maximum=1, name="Size")

    with pytest.raises(BadRequestError, match="Size"):
        await modifier_rules.resolve(
            _db([link]), product=_product("Latte"), selections=[]
        )


async def test_an_optional_group_left_alone_is_fine():
    link = _link([_option("Syrup", "3.00")], minimum=0, maximum=2)

    assert (
        await modifier_rules.resolve(
            _db([link]), product=_product("Latte"), selections=[]
        )
        == []
    )


async def test_a_quantity_below_one_is_refused():
    option = _option("Ferrero")
    link = _link([option], maximum=3)

    with pytest.raises(BadRequestError, match="at least 1"):
        await modifier_rules.resolve(
            _db([link]),
            product=_product(),
            selections=[modifier_rules.Selection(option_id=option.id, quantity=0)],
        )
