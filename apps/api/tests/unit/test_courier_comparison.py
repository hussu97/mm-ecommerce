"""
Pricing a zone live between noon Send and a Slider bike, and picking the cheaper.

A branch-row whose own courier is one of the pair (`noon_send` / `slider_bike`)
and whose `alternate_providers` names the other is *comparable*: both couriers
can reach the ground, so the run is quoted on both and the cheaper wins, a tie
going to noon Send (no network call, no Slider rate-limit). Everything else — a
`slider_car` / `lalamove` / `third_party` row, or one that lists only the other
escapes — is priced against its single courier exactly as before, so the change
is invisible outside the pair.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.couriers import courier_service, noon_send_service, slider_service
from app.services.couriers.lalamove_service import Estimate

NOON = courier_service.NOON_SEND
BIKE = courier_service.SLIDER_BIKE
CAR = courier_service.SLIDER_CAR
LALA = courier_service.LALAMOVE
TP = courier_service.THIRD_PARTY


def _est(cost: str) -> Estimate:
    return Estimate(
        cost=Decimal(cost), currency="AED", distance_m=4000, quotation_id=None
    )


# ── comparison_candidates ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "provider, alternates, expected",
    [
        (NOON, [BIKE, LALA], (NOON, BIKE)),
        (BIKE, [NOON, LALA], (NOON, BIKE)),
        (NOON, (BIKE,), (NOON, BIKE)),
        # Not comparable: the pair-mate is absent.
        (NOON, [LALA], None),
        (BIKE, [CAR, LALA], None),
        # Not comparable: the row's own courier is not in the pair.
        (CAR, [BIKE, LALA], None),
        (LALA, [NOON, BIKE], None),
        (TP, [BIKE], None),
        (None, [BIKE], None),
        # Degenerate alternates.
        (NOON, None, None),
        (NOON, [], None),
    ],
)
def test_comparison_candidates(provider, alternates, expected):
    assert courier_service.comparison_candidates(provider, alternates) == expected


# ── resolve_and_estimate: the comparable path ───────────────────────────────


@pytest.fixture
def both_enabled(monkeypatch):
    monkeypatch.setattr(noon_send_service, "is_enabled", lambda: True)
    monkeypatch.setattr(slider_service, "is_enabled", lambda: True)


def _stub_estimators(monkeypatch, *, noon, bike):
    """Stub each courier's `estimate_for_point` to a fixed `(estimate, error)`."""
    calls: dict[str, dict] = {}

    async def _noon(db, lat, lng, address=None, branch_id=None, pickup=None):
        calls["noon"] = {"lat": lat, "lng": lng}
        return noon

    async def _bike(db, lat, lng, address=None, branch_id=None, pickup=None, **kw):
        calls["bike"] = {"lat": lat, "lng": lng, **kw}
        return bike

    monkeypatch.setattr(noon_send_service, "estimate_for_point", _noon)
    monkeypatch.setattr(slider_service, "estimate_for_point", _bike)
    return calls


async def _resolve(**over):
    kwargs = {
        "provider": BIKE,
        "alternate_providers": [NOON, LALA],
        "latitude": 25.33,
        "longitude": 55.37,
        "address": "somewhere",
        "branch_id": None,
        "zone_name": "Sharjah · Al Taawun",
    }
    kwargs.update(over)
    return await courier_service.resolve_and_estimate(None, **kwargs)


@pytest.mark.asyncio
async def test_noon_wins_when_cheaper(monkeypatch, both_enabled):
    _stub_estimators(
        monkeypatch, noon=(_est("13.00"), None), bike=(_est("15.31"), None)
    )
    estimate, error, chosen = await _resolve()
    assert chosen == NOON
    assert estimate.cost == Decimal("13.00")
    assert error is None


@pytest.mark.asyncio
async def test_bike_wins_when_cheaper(monkeypatch, both_enabled):
    _stub_estimators(
        monkeypatch, noon=(_est("12.00"), None), bike=(_est("10.00"), None)
    )
    estimate, error, chosen = await _resolve()
    assert chosen == BIKE
    assert estimate.cost == Decimal("10.00")


@pytest.mark.asyncio
async def test_tie_breaks_to_noon(monkeypatch, both_enabled):
    _stub_estimators(
        monkeypatch, noon=(_est("12.00"), None), bike=(_est("12.00"), None)
    )
    _estimate, _error, chosen = await _resolve()
    assert chosen == NOON


@pytest.mark.asyncio
async def test_bike_only_when_noon_out_of_range(monkeypatch, both_enabled):
    # noon Send refuses past 20 km; the bike still carries it.
    _stub_estimators(
        monkeypatch,
        noon=(None, "24.0 km is past noon Send's 20 km limit"),
        bike=(_est("28.71"), None),
    )
    estimate, _error, chosen = await _resolve()
    assert chosen == BIKE
    assert estimate.cost == Decimal("28.71")


@pytest.mark.asyncio
async def test_noon_only_when_bike_unavailable(monkeypatch, both_enabled):
    _stub_estimators(
        monkeypatch,
        noon=(_est("12.00"), None),
        bike=(None, "Slider quoted no bike fare for this address"),
    )
    estimate, _error, chosen = await _resolve()
    assert chosen == NOON
    assert estimate.cost == Decimal("12.00")


@pytest.mark.asyncio
async def test_slider_priced_as_bike(monkeypatch, both_enabled):
    calls = _stub_estimators(
        monkeypatch, noon=(_est("13.00"), None), bike=(_est("11.00"), None)
    )
    await _resolve()
    # The Slider side is always quoted as a bike, never a car.
    assert calls["bike"].get("vehicle") == "bike"
    assert calls["bike"].get("drop_emirate") == "Sharjah · Al Taawun"


@pytest.mark.asyncio
async def test_neither_serviceable_is_unserviceable(monkeypatch, both_enabled):
    _stub_estimators(
        monkeypatch,
        noon=(None, "past noon Send's 20 km limit"),
        bike=(None, "Slider quoted no bike fare"),
    )
    estimate, error, chosen = await _resolve()
    assert estimate is None
    assert error is not None
    # Degrades to the row's own resolved courier rather than inventing a price.
    assert chosen == BIKE


@pytest.mark.asyncio
async def test_disabled_courier_drops_out(monkeypatch):
    # Slider unconfigured: only noon Send is a candidate, and it wins if it can
    # serve — no Slider fare call is made.
    monkeypatch.setattr(noon_send_service, "is_enabled", lambda: True)
    monkeypatch.setattr(slider_service, "is_enabled", lambda: False)

    async def _boom(*a, **k):  # pragma: no cover - must not be called
        raise AssertionError("Slider must not be quoted when disabled")

    async def _noon(db, lat, lng, address=None, branch_id=None, pickup=None):
        return _est("12.00"), None

    monkeypatch.setattr(slider_service, "estimate_for_point", _boom)
    monkeypatch.setattr(noon_send_service, "estimate_for_point", _noon)
    estimate, _error, chosen = await _resolve()
    assert chosen == NOON
    assert estimate.cost == Decimal("12.00")


# ── resolve_and_estimate: the single-courier path is unchanged ───────────────


@pytest.mark.asyncio
async def test_non_comparable_delegates_to_single_estimate(monkeypatch):
    """A `slider_car` / `lalamove` / single-provider row calls exactly one
    estimator through `estimate_for_point`, exactly as before."""
    seen = {}
    # Slider configured, so `effective_provider` passes `slider_car` through
    # rather than resolving it to a fallback.
    monkeypatch.setattr(slider_service, "is_enabled", lambda: True)

    async def _single(db, provider, lat, lng, address=None, branch_id=None, zone=None):
        seen["provider"] = provider
        return _est("21.00"), None

    monkeypatch.setattr(courier_service, "estimate_for_point", _single)

    estimate, error, chosen = await courier_service.resolve_and_estimate(
        None,
        provider=CAR,
        alternate_providers=[LALA],
        latitude=25.0,
        longitude=55.0,
        zone_name="Dubai · Marina",
    )
    assert seen["provider"] == CAR
    assert estimate.cost == Decimal("21.00")
    assert chosen == CAR  # effective_provider passes a non-Slider-fallback through
    assert error is None
