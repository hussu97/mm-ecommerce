"""`modifier_rules.resolve` and `availability_service.blocking_groups` must agree
on a required group with no active option (F-INV-8).

They used to disagree: `blocking_groups` skips an inactive modifier and calls the
product sellable, while `resolve` enforced the inactive group's minimum against
zero pickable options and made the product impossible to order everywhere. These
pin the two paths to the same verdict in every combination.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError
from app.services.catalog import modifier_rules
from app.services.catalog.availability_service import BranchAvailability


def _option(name: str, *, active: bool = True, price: str = "0", order: int = 0):
    return SimpleNamespace(
        id=uuid4(),
        name=name,
        sku=f"sku-{name.lower()}",
        price=Decimal(price),
        translations={},
        display_order=order,
        is_active=active,
    )


def _link(options, *, minimum=1, maximum=1, active=True, name="Filling"):
    return SimpleNamespace(
        id=uuid4(),
        modifier_id=uuid4(),
        minimum_options=minimum,
        maximum_options=maximum,
        free_options=0,
        unique_options=False,
        display_order=0,
        modifier=SimpleNamespace(
            name=name, translations={}, is_active=active, options=options
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


def _product(links):
    return SimpleNamespace(id=uuid4(), name="Box of one", product_modifiers=links)


def _availability():
    """A branch with nothing marked out — so only catalogue state matters."""
    return BranchAvailability(branch_id=uuid4())


@pytest.mark.asyncio
async def test_required_group_with_inactive_modifier_is_sellable_on_both_paths():
    # A required group whose modifier is switched off: off the menu entirely.
    link = _link([_option("Ferrero", active=True)], minimum=1, active=False)
    product = _product([link])

    assert _availability().blocking_groups(product) == []
    # No BadRequestError: the product can be ordered without picking from it.
    assert await modifier_rules.resolve(_db([link]), product=product, selections=[]) == []


@pytest.mark.asyncio
async def test_required_group_with_all_options_inactive_blocks_on_both_paths():
    # Modifier is active, but every option is retired: nothing left to choose.
    link = _link(
        [_option("Ferrero", active=False), _option("Fudge", active=False)],
        minimum=1,
        active=True,
    )
    product = _product([link])

    assert _availability().blocking_groups(product) == [link]
    with pytest.raises(BadRequestError):
        await modifier_rules.resolve(_db([link]), product=product, selections=[])


@pytest.mark.asyncio
async def test_required_group_with_a_live_option_is_sellable_on_both_paths():
    live = _option("Ferrero", active=True)
    link = _link([live, _option("Fudge", active=False)], minimum=1, active=True)
    product = _product([link])

    assert _availability().blocking_groups(product) == []
    resolved = await modifier_rules.resolve(
        _db([link]),
        product=product,
        selections=[modifier_rules.Selection(option_id=live.id, quantity=1)],
    )
    assert [r.option_id for r in resolved] == [live.id]
