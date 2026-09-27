"""
A rank-2 order is quoted from its own kitchen, not the basket's.

The basket's parked courier quote is always the rank-1 kitchen's
(`delivery_service.price` prices `zone.branch_id`). When the rank-1 kitchen
cannot make the basket and the order falls through to rank-2, carrying that
figure onto the delivery row records the wrong kitchen's distance and cost
against a different courier — MM-20260927-001 went to Barsha on a Slider car
carrying Sharjah's 4 km noon Send quote. Creation re-quotes for the kitchen that
actually won, and never falls back to the basket's figure when it does.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.couriers import courier_service, lalamove_service
from app.services.couriers.lalamove_service import Estimate
from app.services.delivery.delivery_zone_service import Zone
from app.services.orders import order_service
from app.services.orders.order_service import FulfilmentChoice

SHARJAH = uuid.uuid4()
BARSHA = uuid.uuid4()
PIN = {"latitude": "25.307", "longitude": "55.366", "address_line_1": "Al Taawun"}


def _zone(branch_id=SHARJAH) -> Zone:
    return Zone(
        id=uuid.uuid4(),
        name="Sharjah · Al Taawun",
        delivery_fee=Decimal("0.00"),
        fulfilment_provider="slider_bike",
        min_lat=25.2,
        max_lat=25.4,
        min_lng=55.3,
        max_lng=55.5,
        rings=(),
        branch_id=branch_id,
    )


def _choice(branch_id, provider, alternates=()) -> FulfilmentChoice:
    return FulfilmentChoice(
        branch=SimpleNamespace(id=branch_id),
        provider=provider,
        alternate_providers=tuple(alternates),
    )


def _est(cost: str, distance_m: int) -> Estimate:
    return Estimate(
        cost=Decimal(cost), currency="AED", distance_m=distance_m, quotation_id=None
    )


@pytest.fixture
def quotes(monkeypatch):
    """Records every live quote and answers with a Barsha-sized fare."""
    calls: list[dict] = []

    async def fake(db, **kwargs):
        calls.append(kwargs)
        return _est("58.00", 41000), None, kwargs["provider"]

    monkeypatch.setattr(courier_service, "resolve_and_estimate", fake)
    return calls


@pytest.mark.asyncio
async def test_rank2_branch_is_quoted_from_its_own_kitchen(quotes):
    provider, estimate, error, stale = await order_service._courier_for_selected_branch(
        None,
        zone=_zone(),
        choice=_choice(BARSHA, "slider_car", ["lalamove"]),
        snapshot=PIN,
    )
    assert len(quotes) == 1
    assert quotes[0]["branch_id"] == BARSHA
    assert quotes[0]["provider"] == "slider_car"
    assert provider == "slider_car"
    assert estimate.distance_m == 41000
    assert error is None
    assert stale is True


@pytest.mark.asyncio
async def test_rank1_single_courier_keeps_the_basket_quote(quotes):
    """The ordinary case costs no courier call."""
    provider, estimate, _, stale = await order_service._courier_for_selected_branch(
        None,
        zone=_zone(),
        choice=_choice(SHARJAH, "slider_car", ["lalamove"]),
        snapshot=PIN,
    )
    assert quotes == []
    assert (provider, estimate, stale) == ("slider_car", None, False)


@pytest.mark.asyncio
async def test_rank1_comparable_row_still_compares_and_may_use_the_basket(quotes):
    """The noon Send / bike comparison is unchanged, and a failed re-quote on the
    rank-1 kitchen may still fall back to the basket — it is this kitchen's."""
    _, _, _, stale = await order_service._courier_for_selected_branch(
        None,
        zone=_zone(),
        choice=_choice(SHARJAH, "slider_bike", ["noon_send", "lalamove"]),
        snapshot=PIN,
    )
    assert len(quotes) == 1
    assert quotes[0]["branch_id"] == SHARJAH
    assert stale is False


@pytest.mark.asyncio
async def test_rank2_without_a_pin_is_stale_with_a_reason(quotes):
    _, estimate, error, stale = await order_service._courier_for_selected_branch(
        None,
        zone=_zone(),
        choice=_choice(BARSHA, "slider_car"),
        snapshot={},
    )
    assert quotes == []
    assert estimate is None
    assert error
    assert stale is True


@pytest.mark.asyncio
async def test_a_zone_without_a_kitchen_is_not_requoted(quotes):
    """A legacy zone with no `branch_id` was priced from the single configured
    branch, which is where it resolves to — no second call."""
    *_, stale = await order_service._courier_for_selected_branch(
        None,
        zone=_zone(branch_id=None),
        choice=_choice(BARSHA, "slider_car"),
        snapshot=PIN,
    )
    assert quotes == []
    assert stale is False


@pytest.mark.asyncio
async def test_no_zone_is_not_quoted(quotes):
    result = await order_service._courier_for_selected_branch(
        None, zone=None, choice=_choice(BARSHA, "lalamove"), snapshot=PIN
    )
    assert quotes == []
    assert result == ("lalamove", None, None, False)


# ── the row it opens ──────────────────────────────────────────────────────────


class _RecordDb:
    def add(self, _row):
        return None

    async def flush(self):
        return None


def _sharjah_basket():
    """What MM-20260927-001's basket parked: Sharjah's noon Send quote."""
    return SimpleNamespace(
        delivery_quote_cost=Decimal("13.50"),
        delivery_quote_currency="AED",
        delivery_quote_distance_m=4052,
        delivery_quote_reference=None,
        delivery_quote_at=None,
        delivery_quote_error=None,
    )


@pytest.mark.asyncio
async def test_a_failed_rank2_quote_records_the_reason_not_the_basket():
    delivery = await lalamove_service.record_order_delivery(
        _RecordDb(),
        SimpleNamespace(id=uuid.uuid4(), delivery_fee=Decimal("0.00")),
        zone=_zone(),
        cart=None,
        provider="slider_car",
        estimate=None,
        error="Slider has no fare for this drop",
    )
    assert delivery.quoted_cost is None
    assert delivery.quoted_distance_m is None
    assert delivery.last_error == "Slider has no fare for this drop"


@pytest.mark.asyncio
async def test_the_basket_quote_still_lands_on_a_rank1_order():
    delivery = await lalamove_service.record_order_delivery(
        _RecordDb(),
        SimpleNamespace(id=uuid.uuid4(), delivery_fee=Decimal("0.00")),
        zone=_zone(),
        cart=_sharjah_basket(),
        provider="noon_send",
        error="ignored: the basket answered",
    )
    assert delivery.quoted_cost == Decimal("13.50")
    assert delivery.quoted_distance_m == 4052
    assert delivery.last_error is None
