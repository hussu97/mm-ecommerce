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
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings
from app.models.order import OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services.couriers import courier_service, lalamove_service
from app.services.delivery.delivery_zone_service import Zone


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


# ── Slider routing ────────────────────────────────────────────────────────────
#
# A `slider` zone goes to Slider whenever Slider is configured — for everyone,
# like the map decides for the other two. The only fallback left is the one they
# all have: an absent credential or a refusal at booking drops the order to
# noon Send inside Sharjah, Lalamove outside it, so a paid order is never
# stranded.


@pytest.fixture
def slider_ready(monkeypatch, configured):
    """Slider configured, so a Slider zone is Slider's."""
    monkeypatch.setattr(settings, "SLIDER_API_KEY", "test-key")
    return settings


@pytest.fixture
def slider_spies(monkeypatch, spies, in_range):
    async def slider_may_serve(db, order):
        return True, None

    async def slider_dispatch(db, order):
        spies.append("slider")
        db.delivery.courier_order_id = "SLD-4820193"
        db.delivery.provider = "slider"
        db.delivery.last_error = None
        return db.delivery

    monkeypatch.setattr(courier_service.slider_service, "may_serve", slider_may_serve)
    monkeypatch.setattr(
        courier_service.slider_service, "dispatch_order", slider_dispatch
    )
    return spies


def _slider_delivery(zone="Ajman City") -> OrderDelivery:
    delivery = _delivery("slider")
    delivery.zone_name = zone
    return delivery


@pytest.mark.asyncio
async def test_a_slider_zone_is_carried_by_slider(slider_ready, slider_spies):
    """The map decides, for everyone: a Slider zone goes to Slider."""
    delivery = _slider_delivery()
    result = await courier_service.dispatch(_Db(delivery), _order())

    assert slider_spies == ["slider"]
    assert result.courier_order_id == "SLD-4820193"
    assert result.original_provider is None


@pytest.mark.asyncio
async def test_any_customer_in_a_slider_zone_gets_slider(slider_ready, slider_spies):
    """A guest, and an address nobody has ever heard of, still ride Slider — the
    assertion the pilot allow-list used to make impossible."""
    await courier_service.dispatch(
        _Db(_slider_delivery()), _order(user_id=None, email="a.stranger@example.com")
    )
    assert slider_spies == ["slider"]


@pytest.mark.asyncio
async def test_an_unconfigured_slider_zone_inside_sharjah_falls_back_to_noon_send(
    slider_ready, monkeypatch, slider_spies
):
    """`Sharjah Core` was carved out of `Sharjah Central`, which is noon Send's,
    so an unconfigured Slider zone there falls back to noon Send, not Lalamove."""
    monkeypatch.setattr(settings, "SLIDER_API_KEY", "")

    async def dispatched(db, order):
        db.delivery.courier_order_id = "EHG84NNJMVG35BTDE"
        slider_spies.append("noon_send")
        return db.delivery

    monkeypatch.setattr(courier_service.noon_send_service, "dispatch_order", dispatched)
    result = await courier_service.dispatch(
        _Db(_slider_delivery(zone="Sharjah Core")), _order()
    )

    assert slider_spies == ["noon_send"]
    assert result.provider == "noon_send"
    assert result.original_provider is None


@pytest.mark.asyncio
async def test_an_unconfigured_key_is_a_fallback_not_an_outage(
    slider_ready, monkeypatch, slider_spies
):
    """The same contract the other two couriers already have — an absent
    credential falls a Slider zone back (Ajman to Lalamove) rather than failing."""
    monkeypatch.setattr(settings, "SLIDER_API_KEY", "")
    result = await courier_service.dispatch(_Db(_slider_delivery()), _order())
    assert slider_spies == ["lalamove"]
    assert result.courier_order_id
    assert result.original_provider is None


@pytest.mark.asyncio
async def test_a_slider_refusal_falls_through_rather_than_stranding_the_order(
    slider_ready, monkeypatch, slider_spies
):
    """
    Slider publishes no serviceability endpoint and their fare call is not one —
    it priced Riyadh, Muscat and Liwa. An address outside their area is only
    ever discovered as a 422 at creation, on an order that is already paid for
    and boxed.
    """

    async def refused(db, order):
        slider_spies.append("slider")
        db.delivery.last_error = "Slider: outside the service area"
        return db.delivery

    monkeypatch.setattr(courier_service.slider_service, "dispatch_order", refused)
    result = await courier_service.dispatch(_Db(_slider_delivery()), _order())

    assert slider_spies == ["slider", "lalamove"]
    assert result.courier_order_id
    assert not result.needs_attention


@pytest.mark.asyncio
async def test_the_cod_ceiling_is_a_refusal_before_a_rider_is_engaged(
    slider_ready, monkeypatch, slider_spies
):
    """A rejection after a rider has been engaged is a cancellation we pay for."""

    async def over_the_ceiling(db, order):
        return False, "AED 600.00 is over Slider's card ceiling of AED 500.00"

    monkeypatch.setattr(courier_service.slider_service, "may_serve", over_the_ceiling)
    await courier_service.dispatch(
        _Db(_slider_delivery()), _order(total=Decimal("600.00"))
    )
    assert slider_spies == ["lalamove"]


@pytest.mark.asyncio
async def test_a_lalamove_zone_is_never_offered_to_slider(slider_ready, slider_spies):
    """The map decides which zones are Slider's, and a Lalamove zone is not one."""
    await courier_service.dispatch(_Db(_delivery("lalamove")), _order())
    assert slider_spies == ["lalamove"]


# ── the decision as a primitive ───────────────────────────────────────────────
#
# `carrier_for`, the fare quote and the order-creation stamp are three callers of
# one decision that must not disagree — a quote against a courier the dispatcher
# will not book is a fare nobody rides. The decision itself is
# `effective_provider`, expressed in strings so all three can reach it. These pin
# it directly.


def test_effective_provider_sends_a_slider_zone_to_slider_when_configured(slider_ready):
    provider, reason = courier_service.effective_provider("slider", "Ajman City")
    assert provider == "slider"
    assert reason is None


def test_effective_provider_falls_a_slider_zone_back_when_unconfigured(monkeypatch):
    """An absent credential is a fallback, chosen off the zone name: Ajman was
    Lalamove's, `Sharjah Core` was noon Send's."""
    monkeypatch.setattr(settings, "SLIDER_API_KEY", "")
    ajman, reason = courier_service.effective_provider("slider", "Ajman City")
    assert ajman == "lalamove"
    assert reason  # and it says why it is not the zone's own
    sharjah, _ = courier_service.effective_provider("slider", "Sharjah Core")
    assert sharjah == "noon_send"


@pytest.mark.parametrize("zone_provider", ["lalamove", "noon_send", "third_party"])
def test_effective_provider_leaves_a_non_slider_zone_alone(slider_ready, zone_provider):
    provider, reason = courier_service.effective_provider(zone_provider, "Dubai Near")
    assert provider == zone_provider
    assert reason is None


# ── the decision at the fare quote ─────────────────────────────────────────────
#
# `effective_provider` runs before a fare is quoted too, so a Slider zone is
# priced against Slider when it is configured, and against its fallback when it
# is not — never against a Slider endpoint the order will not reach.


@pytest.fixture
def estimate_spies(monkeypatch, slider_ready):
    """Records which courier was asked to price, without reaching any API."""
    calls: list[str] = []

    def spy(name):
        async def _estimate(*_args, **_kwargs):
            calls.append(name)
            return None, None

        return _estimate

    for service in (
        courier_service.slider_service,
        courier_service.lalamove_service,
        courier_service.noon_send_service,
    ):
        monkeypatch.setattr(service, "is_enabled", lambda: True)
    monkeypatch.setattr(
        courier_service.slider_service, "estimate_for_point", spy("slider")
    )
    monkeypatch.setattr(
        courier_service.lalamove_service, "estimate_for_point", spy("lalamove")
    )
    monkeypatch.setattr(
        courier_service.noon_send_service, "estimate_for_point", spy("noon_send")
    )
    return calls


@pytest.mark.asyncio
async def test_a_slider_zone_quotes_against_slider(estimate_spies):
    await courier_service.estimate_for_point(
        AsyncMock(),
        "slider",
        25.40,
        55.44,
        zone_name="Ajman City",
    )
    assert estimate_spies == ["slider"]


@pytest.mark.asyncio
async def test_an_unconfigured_slider_zone_quotes_against_its_fallback(
    estimate_spies, monkeypatch
):
    monkeypatch.setattr(courier_service.slider_service, "is_enabled", lambda: False)
    await courier_service.estimate_for_point(
        AsyncMock(),
        "slider",
        25.40,
        55.44,
        zone_name="Ajman City",
    )
    assert estimate_spies == ["lalamove"]


# ── the decision at order creation ─────────────────────────────────────────────
#
# The row an order opens carries the courier it will actually dispatch to,
# resolved through the same decision and handed to `record_order_delivery`. The
# zone still travels whole so batching keeps hold of its schedule.


def _zone(name="Ajman City", provider="slider"):
    return Zone(
        id=uuid.uuid4(),
        name=name,
        delivery_fee=Decimal("10.00"),
        fulfilment_provider=provider,
        min_lat=25.3,
        max_lat=25.5,
        min_lng=55.4,
        max_lng=55.6,
        rings=(),
        free_delivery_eligible=True,
        free_delivery_threshold=Decimal("75.00"),
    )


class _RecordDb:
    """Just enough session to accept the row `record_order_delivery` writes."""

    def add(self, _row):
        return None

    async def flush(self):
        return None


def _quoted_cart(error=None):
    """A basket whose quote succeeded — a cost parked, no error — which is what a
    gated Slider zone's Lalamove quote leaves behind."""
    return SimpleNamespace(
        delivery_quote_cost=Decimal("31.00"),
        delivery_quote_currency="AED",
        delivery_quote_distance_m=9000,
        delivery_quote_reference="q_1",
        delivery_quote_at=None,
        delivery_quote_error=error,
    )


@pytest.mark.asyncio
async def test_a_fallback_order_opens_its_row_on_the_resolved_courier():
    """A Slider zone with Slider unconfigured stamps the courier that will carry
    it, and no Slider error is parked because the quote never reached Slider."""
    zone = _zone()
    delivery = await lalamove_service.record_order_delivery(
        _RecordDb(),
        SimpleNamespace(id=uuid.uuid4(), delivery_fee=Decimal("10.00")),
        zone=zone,
        cart=_quoted_cart(),
        provider="lalamove",
    )
    assert delivery.provider == "lalamove"
    # The real zone travels whole: batching reaches this zone's own schedule
    # through the name and the id, whatever courier the provider resolved to.
    assert delivery.zone_name == "Ajman City"
    assert delivery.polygon_id == zone.id
    assert delivery.last_error is None


@pytest.mark.asyncio
async def test_a_slider_zone_opens_its_row_on_slider():
    delivery = await lalamove_service.record_order_delivery(
        _RecordDb(),
        SimpleNamespace(id=uuid.uuid4(), delivery_fee=Decimal("10.00")),
        zone=_zone(),
        cart=_quoted_cart(),
        provider="slider",
    )
    assert delivery.provider == "slider"


@pytest.mark.asyncio
async def test_an_unstamped_row_falls_back_to_the_zone_provider():
    """No provider passed takes the zone's own courier — the identity for every
    non-Slider zone."""
    delivery = await lalamove_service.record_order_delivery(
        _RecordDb(),
        SimpleNamespace(id=uuid.uuid4(), delivery_fee=Decimal("10.00")),
        zone=_zone(provider="lalamove", name="Dubai Near"),
        cart=_quoted_cart(),
    )
    assert delivery.provider == "lalamove"
