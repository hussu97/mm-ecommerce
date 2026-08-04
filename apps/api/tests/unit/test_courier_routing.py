"""
Who actually ends up carrying an order.

Two rules decide it, and both exist to make a new courier safe to switch on
against real, paid orders.

**The allow-list.** On production, noon Send only carries orders placed by a
signed-in customer whose own email is on the list. That is a deliberate
live-fire trial, and the failure mode it guards against — an unproven courier
quietly taking every Sharjah order — is exactly the kind of thing that works in
staging and is discovered on a Friday evening.

**The fallback.** noon Send caps a run at 15 km and can simply have nobody free.
A refusal must never strand a paid, packed order, so it goes out on Lalamove
instead. That also means the zone boundary is an optimisation rather than a
correctness requirement: drawing it slightly too wide costs a swap, not a
delivery.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.models.order_delivery import OrderDelivery
from app.services import courier_service

TRIAL_EMAIL = "h_abbasi97@hotmail.com"


def _order(**overrides):
    values = {
        "id": uuid.uuid4(),
        "order_number": "MM-1001",
        "email": TRIAL_EMAIL,
        "user_id": uuid.uuid4(),
        "total": Decimal("185.00"),
        "payment_method": "stripe",
        "notes": None,
        "shipping_address_snapshot": {
            "latitude": 25.3213,
            "longitude": 55.3820,
            "phone": "+971501234567",
            "first_name": "Hussain",
            "last_name": "Abbasi",
            "address_line_1": "Garden Tower 1, Al Majaz 3",
            "unit_number": "1",
            "city": "Sharjah",
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _delivery(provider="noon_send") -> OrderDelivery:
    return OrderDelivery(
        order_id=uuid.uuid4(),
        provider=provider,
        zone_name="Sharjah Central",
        fee_charged=Decimal("15.00"),
    )


@pytest.fixture
def configured(monkeypatch):
    """A working noon Send, so the gate is the only thing under test."""
    # The API key alone decides whether noon Send is configured. The outlet is a
    # branch column, so it belongs to whatever pickup the zone resolves to.
    monkeypatch.setattr(settings, "NOON_SEND_API_KEY", "test-key")
    monkeypatch.setattr(settings, "TRIAL_CUSTOMER_EMAILS", TRIAL_EMAIL)
    return settings


# ── the trial list ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("app_env", ["development", "staging", "production"])
@pytest.mark.parametrize("noon_env", ["staging", "production"])
def test_the_list_is_the_only_gate_in_every_environment(
    configured, monkeypatch, app_env, noon_env
):
    """
    The gate deliberately does not read either environment setting.

    Production currently points `NOON_SEND_ENV` at noon's *staging* fleet, so a
    gate written as "apply the list only on production" would have opened the
    trial to every customer the moment that was configured — silently, and on
    real orders. The list applies everywhere or it is not a gate.
    """
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    monkeypatch.setattr(settings, "NOON_SEND_ENV", noon_env)

    assert courier_service.may_use_noon_send(_order())[0]
    assert not courier_service.may_use_noon_send(_order(email="other@example.com"))[0]


def test_the_trial_account_is_carried(configured):
    allowed, reason = courier_service.may_use_noon_send(_order())
    assert allowed and reason is None


def test_an_address_is_matched_case_insensitively(configured):
    """An address typed with a capital is the same address."""
    assert courier_service.may_use_noon_send(_order(email="H_Abbasi97@Hotmail.com"))[0]


def test_a_guest_is_refused_even_at_the_right_address(configured):
    """
    "Signed in" is half the rule, and it is the half a guest checkout can forge:
    anybody may type the trial address into the email box.
    """
    allowed, reason = courier_service.may_use_noon_send(_order(user_id=None))
    assert not allowed
    assert "signed-in" in reason


def test_a_different_customer_is_refused(configured):
    allowed, reason = courier_service.may_use_noon_send(
        _order(email="someone@example.com")
    )
    assert not allowed
    assert "trial account" in reason


def test_an_empty_list_opens_noon_send_to_everyone(configured, monkeypatch):
    """How the trial ends: clear the list rather than edit code."""
    monkeypatch.setattr(settings, "TRIAL_CUSTOMER_EMAILS", "")
    allowed, _ = courier_service.may_use_noon_send(_order(email="anyone@example.com"))
    assert allowed


def test_several_addresses_may_be_listed(configured, monkeypatch):
    monkeypatch.setattr(
        settings, "TRIAL_CUSTOMER_EMAILS", f"{TRIAL_EMAIL}, second@example.com"
    )
    assert courier_service.may_use_noon_send(_order(email="second@example.com"))[0]
    assert not courier_service.may_use_noon_send(_order(email="third@example.com"))[0]


def test_unconfigured_credentials_are_a_refusal_not_a_crash(monkeypatch):
    monkeypatch.setattr(settings, "NOON_SEND_API_KEY", "")
    allowed, reason = courier_service.may_use_noon_send(_order())
    assert not allowed
    assert "not configured" in reason


# ── the fallback ──────────────────────────────────────────────────────────────


class _Db:
    """Just enough session to hand back one delivery row."""

    def __init__(self, delivery):
        self.delivery = delivery

    async def execute(self, _stmt):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: self.delivery)
        )


@pytest.fixture
def spies(monkeypatch):
    """Records which courier was asked, without calling either."""
    calls: list[str] = []

    async def noon_send_dispatch(db, order):
        calls.append("noon_send")
        return db.delivery

    async def lalamove_dispatch(db, order):
        calls.append("lalamove")
        db.delivery.courier_order_id = "3553185073308324370"
        db.delivery.provider = "lalamove"
        # What the real one does on success, and the part that matters here:
        # a booking that worked clears whatever the last attempt complained
        # about. See `lalamove_service.dispatch_order`.
        db.delivery.last_error = None
        return db.delivery

    monkeypatch.setattr(
        courier_service.noon_send_service, "dispatch_order", noon_send_dispatch
    )
    monkeypatch.setattr(
        courier_service.lalamove_service, "dispatch_order", lalamove_dispatch
    )
    return calls


@pytest.fixture
def in_range(monkeypatch):
    async def may_serve(db, order):
        return True, None

    monkeypatch.setattr(courier_service.noon_send_service, "may_serve", may_serve)


@pytest.mark.asyncio
async def test_a_noon_send_zone_uses_noon_send(
    configured, monkeypatch, spies, in_range
):
    delivery = _delivery()

    async def dispatched(db, order):
        db.delivery.courier_order_id = "EHG84NNJMVG35BTDE"
        spies.append("noon_send")
        return db.delivery

    monkeypatch.setattr(courier_service.noon_send_service, "dispatch_order", dispatched)
    result = await courier_service.dispatch(_Db(delivery), _order())

    assert spies == ["noon_send"]
    assert result.courier_order_id == "EHG84NNJMVG35BTDE"


@pytest.mark.asyncio
async def test_a_customer_off_the_list_is_carried_by_lalamove(
    configured, spies, in_range
):
    delivery = _delivery()

    result = await courier_service.dispatch(
        _Db(delivery), _order(email="someone@example.com")
    )

    # noon Send is never even called for them.
    assert spies == ["lalamove"]
    assert result.provider == "lalamove"
    assert result.courier_order_id


@pytest.mark.asyncio
async def test_a_drop_past_the_cap_is_carried_by_lalamove(
    configured, monkeypatch, spies
):
    async def too_far(db, order):
        return False, "Drop-off is about 18.2 km away, past noon Send's 15 km limit"

    monkeypatch.setattr(courier_service.noon_send_service, "may_serve", too_far)
    result = await courier_service.dispatch(_Db(_delivery()), _order())

    assert spies == ["lalamove"]
    assert result.provider == "lalamove"


@pytest.mark.asyncio
async def test_a_refusal_from_noon_send_falls_through_to_lalamove(
    configured, monkeypatch, spies, in_range
):
    """The order is already paid for. Somebody has to collect it."""
    delivery = _delivery()

    async def refused(db, order):
        spies.append("noon_send")
        db.delivery.last_error = "noon Send: no rider available"
        return db.delivery

    monkeypatch.setattr(courier_service.noon_send_service, "dispatch_order", refused)
    result = await courier_service.dispatch(_Db(delivery), _order())

    assert spies == ["noon_send", "lalamove"]
    assert result.courier_order_id
    # A booking that worked is not a problem, so it must not land on the
    # needs-a-human list just because the first courier said no.
    assert not result.needs_attention


@pytest.mark.asyncio
async def test_a_lalamove_zone_stays_lalamove_for_an_ordinary_customer(
    configured, spies
):
    """
    The zone decides, for everybody who is not the trial account.

    This is the guarantee that keeps the trial invisible: a real customer's
    order goes exactly where the map sends it, and noon Send is never asked
    about a zone it probably cannot reach.
    """
    await courier_service.dispatch(
        _Db(_delivery("lalamove")), _order(email="someone@else.com")
    )
    assert spies == ["lalamove"]


@pytest.mark.asyncio
async def test_the_trial_account_reaches_noon_send_from_a_lalamove_zone(
    configured, monkeypatch, spies, in_range
):
    """
    The trial is the one exception, and it has to be.

    noon Send's staging fleet serves three outlets, all in Dubai, and a task
    cannot cross an emirate boundary — so a trial run leaves Dubai and arrives
    in Dubai, which is a `lalamove` zone on every map we have. Gating on the
    zone would mean the trial account could only exercise noon Send by ordering
    somewhere the staging fleet cannot go.
    """

    async def dispatched(db, order):
        db.delivery.courier_order_id = "EHG84NNJMVG35BTDE"
        spies.append("noon_send")
        return db.delivery

    monkeypatch.setattr(courier_service.noon_send_service, "dispatch_order", dispatched)
    result = await courier_service.dispatch(_Db(_delivery("lalamove")), _order())

    assert spies == ["noon_send"]
    assert result.courier_order_id == "EHG84NNJMVG35BTDE"


@pytest.mark.asyncio
async def test_a_third_party_zone_calls_nobody(configured, spies):
    delivery = _delivery("third_party")
    result = await courier_service.dispatch(_Db(delivery), _order())
    assert spies == []
    assert result is delivery
    assert result.courier_order_id is None
