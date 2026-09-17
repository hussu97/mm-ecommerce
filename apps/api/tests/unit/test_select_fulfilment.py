"""The branch-priority walk that picks the kitchen for a delivery order.

`select_fulfilment` walks a zone's ordered branches and gives the order to the
first one that can make the whole basket. These tests fix the walk's behaviour:
rank order is honoured, an out-of-stock or switched-off branch is skipped, and a
basket no branch can make comes back as a `NoBranchFulfils` naming the closest.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.delivery.delivery_zone_service import Zone, ZoneBranch
from app.services.orders.order_service import (
    FulfilmentChoice,
    NoBranchFulfils,
    select_fulfilment,
)

pytestmark = pytest.mark.anyio


def _branch(*, online=True, active=True, deleted=False):
    return SimpleNamespace(
        id=uuid.uuid4(),
        is_active=active,
        deleted_at=object() if deleted else None,
        receives_online_orders=online,
    )


def _zone(branches: list[tuple[SimpleNamespace, str]]) -> Zone:
    """A zone whose priority list is these (branch, courier) pairs, in order."""
    return Zone(
        id=uuid.uuid4(),
        name="Dubai · Test",
        delivery_fee=0,
        fulfilment_provider=branches[0][1] if branches else "third_party",
        branch_id=branches[0][0].id if branches else None,
        branches=tuple(
            ZoneBranch(branch_id=b.id, rank=i + 1, fulfilment_provider=courier)
            for i, (b, courier) in enumerate(branches)
        ),
        min_lat=0,
        max_lat=1,
        min_lng=0,
        max_lng=1,
        rings=(),
    )


def _db(branches: list[SimpleNamespace]):
    by_id = {b.id: b for b in branches}
    db = SimpleNamespace()
    db.get = AsyncMock(side_effect=lambda _model, bid: by_id.get(bid))
    return db


def _line():
    """One unavailable cart line — enough to block a branch."""
    return SimpleNamespace(product_id=uuid.uuid4(), product_name="Cake")


async def test_rank_one_wins_when_it_can_make_everything():
    b1, b2 = _branch(), _branch()
    zone = _zone([(b1, "noon_send"), (b2, "slider_car")])
    with patch(
        "app.services.orders.order_service.availability_service.unavailable_cart_lines",
        new_callable=AsyncMock,
        return_value=[],
    ):
        choice = await select_fulfilment(_db([b1, b2]), zone, cart=None)
    assert isinstance(choice, FulfilmentChoice)
    assert choice.branch is b1
    assert choice.provider == "noon_send"


async def test_walks_to_rank_two_when_rank_one_is_out():
    b1, b2 = _branch(), _branch()
    zone = _zone([(b1, "noon_send"), (b2, "slider_car")])
    # b1 is out of a line; b2 can make everything.
    per_branch = {b1.id: [_line()], b2.id: []}
    with patch(
        "app.services.orders.order_service.availability_service.unavailable_cart_lines",
        new_callable=AsyncMock,
        side_effect=lambda _db, *, cart, branch_id: per_branch[branch_id],
    ):
        choice = await select_fulfilment(_db([b1, b2]), zone, cart=None)
    assert isinstance(choice, FulfilmentChoice)
    assert choice.branch is b2
    assert choice.provider == "slider_car"


async def test_no_branch_can_make_it_names_the_closest():
    b1, b2 = _branch(), _branch()
    zone = _zone([(b1, "noon_send"), (b2, "slider_car")])
    # b1 blocks two lines, b2 blocks one — b2 is the closer of the two.
    per_branch = {b1.id: [_line(), _line()], b2.id: [_line()]}
    with patch(
        "app.services.orders.order_service.availability_service.unavailable_cart_lines",
        new_callable=AsyncMock,
        side_effect=lambda _db, *, cart, branch_id: per_branch[branch_id],
    ):
        result = await select_fulfilment(_db([b1, b2]), zone, cart=None)
    assert isinstance(result, NoBranchFulfils)
    assert result.best_branch is b2
    assert len(result.unavailable) == 1


async def test_a_switched_off_branch_is_skipped():
    """A branch on the map but with online orders turned off does not win, even
    at rank 1 — the flag is a kill switch that needs no map edit."""
    b1 = _branch(online=False)
    b2 = _branch(online=True)
    zone = _zone([(b1, "noon_send"), (b2, "slider_car")])
    with patch(
        "app.services.orders.order_service.availability_service.unavailable_cart_lines",
        new_callable=AsyncMock,
        return_value=[],
    ):
        choice = await select_fulfilment(_db([b1, b2]), zone, cart=None)
    assert isinstance(choice, FulfilmentChoice)
    assert choice.branch is b2


async def test_empty_cart_takes_rank_one():
    """No lines means nothing to block, so the preferred branch wins — the
    pre-basket estimate a product card shows."""
    b1, b2 = _branch(), _branch()
    zone = _zone([(b1, "noon_send"), (b2, "slider_car")])
    with patch(
        "app.services.orders.order_service.availability_service.unavailable_cart_lines",
        new_callable=AsyncMock,
        return_value=[],
    ):
        choice = await select_fulfilment(_db([b1, b2]), zone, cart=None)
    assert choice.branch is b1


async def test_legacy_zone_with_no_priority_falls_back_to_resolve_branch():
    """A zone drawn before multi-branch has no priority list; the single-branch
    resolution still names a kitchen so the order can be written."""
    legacy = _branch()
    zone = Zone(
        id=uuid.uuid4(),
        name="Old",
        delivery_fee=0,
        fulfilment_provider="lalamove",
        branch_id=legacy.id,
        branches=(),  # no assignments
        min_lat=0,
        max_lat=1,
        min_lng=0,
        max_lng=1,
        rings=(),
    )
    with patch(
        "app.services.orders.order_service.resolve_branch",
        new_callable=AsyncMock,
        return_value=legacy,
    ):
        choice = await select_fulfilment(_db([legacy]), zone, cart=None)
    assert isinstance(choice, FulfilmentChoice)
    assert choice.branch is legacy
    assert choice.provider == "lalamove"
