"""
Who actually ends up carrying an order.

**The map decides.** A `noon_send` zone goes to noon Send, a `lalamove` zone to
Lalamove, a `third_party` zone to nobody. Which customer placed the order, and
whether they were signed in at all, decides nothing.

It briefly decided everything. While the integration was being proved on
production, a named allow-list was the only thing that let a real order reach
noon's fleet, and one account was routed to a fixed Dubai outlet regardless of
its zone. Both are gone, and the tests that pinned them are gone with them —
what is left is the guarantee that replaced them, which is that routing is a
property of the polygon and of nothing else.

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
from app.models.order import OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services import courier_service


def _order(**overrides):
    values = {
        "id": uuid.uuid4(),
        "order_number": "MM-1001",
        # Already packed, which is what an order being dispatched used to be by
        # definition and is still a state it can reach — an admin marking it so
        # in the console is the backstop trigger. It matters here because a
        # successful booking now stamps `packed` itself, and an order that is
        # already there stops at the transition guard rather than reaching for a
        # database this test does not have.
        "status": OrderStatusEnum.PACKED,
        "branch_id": None,
        "email": "customer@example.com",
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
        courier_reference="4820193",
    )


@pytest.fixture
def configured(monkeypatch):
    """A working noon Send, so routing is the only thing under test."""
    # The API key alone decides whether noon Send is configured. The outlet is a
    # branch column, so it belongs to whatever pickup the zone resolves to.
    monkeypatch.setattr(settings, "NOON_SEND_API_KEY", "test-key")
    return settings


# ── who may use noon Send ─────────────────────────────────────────────────────


@pytest.mark.parametrize("app_env", ["development", "staging", "production"])
@pytest.mark.parametrize("noon_env", ["staging", "production"])
def test_every_customer_may_use_noon_send_in_every_environment(
    configured, monkeypatch, app_env, noon_env
):
    """
    Nothing about the customer, and nothing about the environment, narrows this.

    Both used to. The environment settings were deliberately kept out of the
    gate so that pointing `NOON_SEND_ENV` at a different fleet could not widen a
    trial by accident; now there is no trial to widen, and the same test says so
    from the other direction.
    """
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    monkeypatch.setattr(settings, "NOON_SEND_ENV", noon_env)

    assert courier_service.may_use_noon_send(_order())[0]
    assert courier_service.may_use_noon_send(_order(email="anyone@example.com"))[0]


def test_a_guest_may_use_noon_send_too(configured):
    """Being signed in was half of the old rule. It is no part of this one."""
    allowed, reason = courier_service.may_use_noon_send(_order(user_id=None))
    assert allowed and reason is None


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
async def test_any_customer_in_a_noon_send_zone_gets_noon_send(
    configured, monkeypatch, spies, in_range
):
    """
    A guest, and an address nobody has ever heard of. This is the assertion the
    allow-list used to make impossible.
    """

    async def dispatched(db, order):
        db.delivery.courier_order_id = "EHG84NNJMVG35BTDE"
        spies.append("noon_send")
        return db.delivery

    monkeypatch.setattr(courier_service.noon_send_service, "dispatch_order", dispatched)
    await courier_service.dispatch(
        _Db(_delivery()), _order(user_id=None, email="a.stranger@example.com")
    )

    assert spies == ["noon_send"]


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
async def test_a_lalamove_zone_is_never_offered_to_noon_send(configured, spies):
    """
    The map decides, and it decides both ways: noon Send is never asked about a
    zone it probably cannot reach, whoever placed the order.
    """
    for email in ("someone@else.com", "was-the-trial-account@example.com"):
        spies.clear()
        await courier_service.dispatch(_Db(_delivery("lalamove")), _order(email=email))
        assert spies == ["lalamove"]


@pytest.mark.asyncio
async def test_a_third_party_zone_calls_nobody(configured, spies):
    delivery = _delivery("third_party")
    result = await courier_service.dispatch(_Db(delivery), _order())
    assert spies == []
    assert result is delivery
    assert result.courier_order_id is None
