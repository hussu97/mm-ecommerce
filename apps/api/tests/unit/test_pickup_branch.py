"""
A collection order is a promise to walk into a specific shop.

Before this, the customer chose "collect" and the API chose the shop — the
storefront showed one hardcoded pin that had no connection to whichever branch
`resolve_branch` happened to land on. Now the order carries the branch the
customer picked, and the two cannot disagree.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.branch import Branch
from app.services import order_service


def _db_returning(branch: Branch | None) -> AsyncMock:
    """A session whose every query answers with this one branch."""
    result = MagicMock()
    result.scalars.return_value.first.return_value = branch
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _branch(reference: str = "K001", **overrides) -> Branch:
    branch = Branch(
        name="Melting Moments Cakes",
        reference=reference,
        address="Garden Tower 1, Al Majaz 3, Sharjah",
        city="Sharjah",
        latitude=25.3304139,
        longitude=55.3736131,
        offers_pickup=True,
        is_active=True,
    )
    branch.id = uuid.uuid4()
    for key, value in overrides.items():
        setattr(branch, key, value)
    return branch


@pytest.mark.asyncio
async def test_the_branch_the_customer_chose_wins_over_every_other_rule():
    """
    For a collection order this is not a guess at all — somebody said where they
    are driving, and the place they arrive at has to be the place the box is.
    """
    chosen = _branch("B001")
    with patch(
        "app.services.order_service.fulfilment_service.pickup_branches",
        new=AsyncMock(return_value=[_branch("K001"), chosen]),
    ):
        resolved = await order_service.resolve_branch(
            AsyncMock(), None, pickup_branch_id=chosen.id
        )
    assert resolved is chosen


@pytest.mark.asyncio
async def test_a_branch_that_does_not_offer_collection_is_refused_not_trusted():
    """
    The id arrives in a request body, so it is checked against the list rather
    than looked up directly. A stale or hand-edited one falls through to the
    ordinary resolution instead of sending somebody to a warehouse.
    """
    fallback = _branch("K001")
    with (
        patch(
            "app.services.order_service.fulfilment_service.pickup_branches",
            new=AsyncMock(return_value=[fallback]),
        ),
        patch(
            "app.services.order_service.lalamove_service.resolve_pickup",
            new=AsyncMock(return_value=None),
        ),
    ):
        resolved = await order_service.resolve_branch(
            _db_returning(fallback), None, pickup_branch_id=uuid.uuid4()
        )
    assert resolved is fallback


@pytest.mark.asyncio
async def test_a_delivery_order_never_consults_the_collection_list():
    """
    `create_order` only passes the id for a collection order, so a delivery is
    still resolved by the zone that priced it — which is the branch that has to
    bake it, whatever the browser sent.
    """
    zone_branch = _branch("K001")
    pickup_branches = AsyncMock(return_value=[_branch("B001")])
    with (
        patch(
            "app.services.order_service.fulfilment_service.pickup_branches",
            new=pickup_branches,
        ),
        patch(
            "app.services.order_service.lalamove_service.resolve_pickup",
            new=AsyncMock(return_value=None),
        ),
    ):
        resolved = await order_service.resolve_branch(_db_returning(zone_branch), None)

    assert resolved is zone_branch
    pickup_branches.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_collection_list_needs_a_pin_as_well_as_the_flag():
    """
    The whole point of offering the choice is an address and a map link, and a
    branch with no coordinates can give neither. The rule is the `WHERE` clause,
    so that is what is asserted — against the compiled SQL rather than a
    database, because there is no behaviour here beyond the query.
    """
    from app.services import fulfilment_service

    captured = {}

    result = MagicMock()
    result.scalars.return_value.all.return_value = []

    async def _execute(stmt):
        captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        return result

    db = AsyncMock()
    db.execute = _execute

    assert await fulfilment_service.pickup_branches(db) == []

    sql = " ".join(captured["sql"].split())
    assert "branches.offers_pickup IS true" in sql
    assert "branches.is_active IS true" in sql
    assert "branches.deleted_at IS NULL" in sql
    assert "branches.latitude IS NOT NULL" in sql
    assert "branches.longitude IS NOT NULL" in sql
    # Stable order, so the checkout does not shuffle between page loads.
    assert "ORDER BY branches.display_order, branches.name" in sql


def test_offering_collection_is_not_implied_by_taking_online_orders():
    """
    A production kitchen can bake website orders without being somewhere we
    would send a customer with a cake box and no counter. Defaulting the one
    from the other would put every new kitchen on the checkout the day it
    started taking orders — so the column defaults closed and has to be turned
    on by hand.
    """
    offers_pickup = Branch.__table__.c.offers_pickup
    online = Branch.__table__.c.receives_online_orders
    assert offers_pickup.nullable is False
    assert "false" in str(offers_pickup.server_default.arg).lower()
    assert "true" in str(online.server_default.arg).lower()
