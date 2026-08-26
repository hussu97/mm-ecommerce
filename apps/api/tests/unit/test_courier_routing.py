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


# ── the Slider pilot gate ─────────────────────────────────────────────────────
#
# Six zones on the map name Slider, and for everyone who is not on
# `SLIDER_TRIAL_EMAILS` those zones resolve to the courier that carried them
# before it existed: noon Send inside Sharjah, Lalamove outside it. The point of
# these tests is that the rollout is a **no-op for every customer but one** —
# which is what makes it safe to publish the map before the courier is proven.

TRIAL_EMAIL = "pilot@example.com"


@pytest.fixture
def slider_ready(monkeypatch, configured):
    """Slider configured, one account on the list, and it never gets called."""
    monkeypatch.setattr(settings, "SLIDER_API_KEY", "test-key")
    monkeypatch.setattr(settings, "SLIDER_TRIAL_EMAILS", TRIAL_EMAIL)
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
async def test_the_pilot_account_is_carried_by_slider(slider_ready, slider_spies):
    delivery = _slider_delivery()
    result = await courier_service.dispatch(_Db(delivery), _order(email=TRIAL_EMAIL))

    assert slider_spies == ["slider"]
    assert result.courier_order_id == "SLD-4820193"
    assert result.original_provider is None


@pytest.mark.asyncio
async def test_a_gated_order_is_not_marked_as_moved_by_hand(slider_ready, slider_spies):
    """
    The gate is not a reassignment, and three things downstream would read it as
    one. `fulfilment_service._estimate` is the expensive one: it treats a
    populated `original_provider` as "this order was written against a
    third-party zone" and answers tomorrow-before-10-PM instead of an hour, so a
    gate that filled it would have re-promised every Dubai order in a Slider
    zone — which is almost all of them, since almost all of them are gated.
    """
    delivery = _slider_delivery()
    await courier_service.dispatch(_Db(delivery), _order(email="someone@else.com"))

    assert delivery.provider == "lalamove"
    assert delivery.original_provider is None
    assert not delivery.was_reassigned


@pytest.mark.asyncio
async def test_the_address_is_matched_case_and_space_insensitively(
    slider_ready, slider_spies
):
    await courier_service.dispatch(
        _Db(_slider_delivery()), _order(email=f"  {TRIAL_EMAIL.upper()} ")
    )
    assert slider_spies == ["slider"]


@pytest.mark.asyncio
async def test_everybody_else_in_a_slider_zone_goes_where_they_always_did(
    slider_ready, slider_spies
):
    """
    The whole safety argument, in two lines: Ajman was Lalamove's before the
    zone was drawn, and for a customer who is not on the list it still is.
    """
    delivery = _slider_delivery()
    result = await courier_service.dispatch(
        _Db(delivery), _order(email="someone@else.com")
    )

    assert slider_spies == ["lalamove"]
    assert result.provider == "lalamove"
    # `last_error` is clear, because a booking that worked is not a problem and
    # `needs_attention` must not fill up with routine fallbacks.
    assert not result.needs_attention
    # And `original_provider` is untouched, which is the same rule the noon Send
    # fallback follows. That column means "a human moved this order" and three
    # things read it that way — the admin prints "moved from X", the
    # reassignment dialog treats it as the map's own choice, and
    # `fulfilment_service` reads it as "written against a third-party zone" and
    # answers tomorrow rather than an hour. Setting it here would put a
    # hand-moved badge and a next-day promise on nearly every Dubai order.
    assert result.original_provider is None


@pytest.mark.asyncio
async def test_a_slider_zone_inside_sharjah_falls_back_to_noon_send(
    slider_ready, monkeypatch, slider_spies
):
    """`Sharjah Core` was carved out of `Sharjah Central`, which is noon Send's."""

    async def dispatched(db, order):
        db.delivery.courier_order_id = "EHG84NNJMVG35BTDE"
        slider_spies.append("noon_send")
        return db.delivery

    monkeypatch.setattr(courier_service.noon_send_service, "dispatch_order", dispatched)
    result = await courier_service.dispatch(
        _Db(_slider_delivery(zone="Sharjah Core")), _order(email="someone@else.com")
    )

    assert slider_spies == ["noon_send"]
    assert result.provider == "noon_send"
    assert result.original_provider is None


@pytest.mark.asyncio
async def test_a_guest_typing_the_pilot_address_never_reaches_slider(
    slider_ready, slider_spies
):
    """
    Being signed in is the half of the identity that cannot be forged. An email
    is a string anybody may type into a guest checkout.
    """
    await courier_service.dispatch(
        _Db(_slider_delivery()), _order(user_id=None, email=TRIAL_EMAIL)
    )
    assert slider_spies == ["lalamove"]


@pytest.mark.asyncio
@pytest.mark.parametrize("app_env", ["development", "staging", "production"])
@pytest.mark.parametrize("slider_env", ["staging", "production"])
async def test_no_environment_widens_the_pilot(
    slider_ready, monkeypatch, slider_spies, app_env, slider_env
):
    """
    The gate is the list and nothing else. An environment-shaped gate opens a
    trial to everybody the moment the environment changes, which is the mistake
    the noon Send trial was written to avoid and this one inherits the avoidance.
    """
    monkeypatch.setattr(settings, "APP_ENV", app_env)
    monkeypatch.setattr(settings, "SLIDER_ENV", slider_env)

    await courier_service.dispatch(
        _Db(_slider_delivery()), _order(email="someone@else.com")
    )
    assert slider_spies == ["lalamove"]


@pytest.mark.asyncio
async def test_an_empty_list_ends_the_pilot(slider_ready, monkeypatch, slider_spies):
    monkeypatch.setattr(settings, "SLIDER_TRIAL_EMAILS", "")
    await courier_service.dispatch(_Db(_slider_delivery()), _order(email=TRIAL_EMAIL))
    assert slider_spies == ["lalamove"]


@pytest.mark.asyncio
async def test_an_unconfigured_key_is_a_fallback_not_an_outage(
    slider_ready, monkeypatch, slider_spies
):
    """The same contract the other two couriers already have."""
    monkeypatch.setattr(settings, "SLIDER_API_KEY", "")
    result = await courier_service.dispatch(
        _Db(_slider_delivery()), _order(email=TRIAL_EMAIL)
    )
    assert slider_spies == ["lalamove"]
    assert result.courier_order_id


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
    result = await courier_service.dispatch(
        _Db(_slider_delivery()), _order(email=TRIAL_EMAIL)
    )

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
        _Db(_slider_delivery()), _order(email=TRIAL_EMAIL, total=Decimal("600.00"))
    )
    assert slider_spies == ["lalamove"]


@pytest.mark.asyncio
async def test_a_lalamove_zone_is_never_offered_to_slider(slider_ready, slider_spies):
    """The reverse of the gate: the map still decides which zones are Slider's."""
    await courier_service.dispatch(
        _Db(_delivery("lalamove")), _order(email=TRIAL_EMAIL)
    )
    assert slider_spies == ["lalamove"]


# ── the gate as a primitive ───────────────────────────────────────────────────
#
# `carrier_for`, the fare quote and the order-creation stamp are three callers of
# one decision that must not disagree — a quote against Slider for an account the
# dispatcher hands to Lalamove is a fare nobody is booked at. The decision itself
# is `effective_provider`, expressed in strings so all three can reach it. These
# pin it directly.


def test_effective_provider_hands_a_guest_a_slider_zone_to_lalamove(slider_ready):
    provider, reason = courier_service.effective_provider(
        "slider", "Ajman City", None, None
    )
    assert provider == "lalamove"
    assert reason  # and it says why it is not the zone's own


def test_effective_provider_hands_a_signed_in_stranger_to_lalamove(slider_ready):
    provider, reason = courier_service.effective_provider(
        "slider", "Ajman City", uuid.uuid4(), "someone@else.com"
    )
    assert provider == "lalamove"
    assert reason


def test_effective_provider_keeps_a_sharjah_slider_zone_on_noon_send(slider_ready):
    """`Sharjah Core` was carved out of noon Send's ground, so its fallback is
    noon Send, not Lalamove — decided off the zone name for a guest with no
    address at all."""
    provider, _ = courier_service.effective_provider(
        "slider", "Sharjah Core", None, None
    )
    assert provider == "noon_send"


def test_effective_provider_gives_the_pilot_account_slider(slider_ready):
    provider, reason = courier_service.effective_provider(
        "slider", "Ajman City", uuid.uuid4(), TRIAL_EMAIL
    )
    assert provider == "slider"
    assert reason is None


@pytest.mark.parametrize("zone_provider", ["lalamove", "noon_send", "third_party"])
def test_effective_provider_leaves_a_non_slider_zone_alone(slider_ready, zone_provider):
    provider, reason = courier_service.effective_provider(
        zone_provider, "Dubai Near", uuid.uuid4(), "anyone@example.com"
    )
    assert provider == zone_provider
    assert reason is None


# ── the gate at the fare quote ─────────────────────────────────────────────────
#
# The same gate now runs before a fare is quoted, so a non-pilot Slider zone is
# priced against the courier that will carry it — never against Slider's dead
# sandbox, whose 403 used to be parked on a real order's cart.


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
async def test_a_non_pilot_slider_quote_prices_against_lalamove(estimate_spies):
    await courier_service.estimate_for_point(
        AsyncMock(),
        "slider",
        25.40,
        55.44,
        zone_name="Ajman City",
        user_id=uuid.uuid4(),
        email="someone@else.com",
    )
    assert estimate_spies == ["lalamove"]


@pytest.mark.asyncio
async def test_the_pilot_account_quote_prices_against_slider(estimate_spies):
    await courier_service.estimate_for_point(
        AsyncMock(),
        "slider",
        25.40,
        55.44,
        zone_name="Ajman City",
        user_id=uuid.uuid4(),
        email=TRIAL_EMAIL,
    )
    assert estimate_spies == ["slider"]


# ── the gate at order creation ─────────────────────────────────────────────────
#
# The row an order opens carries the courier it will actually dispatch to,
# resolved through the same gate and handed to `record_order_delivery`. The zone
# still travels whole so batching keeps hold of its schedule.


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
async def test_a_gated_order_opens_its_row_on_the_fallback_courier():
    """A guest in a Slider zone stamps the courier that will carry it, and no
    Slider error is parked because the quote never reached Slider."""
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
async def test_the_pilot_account_opens_its_row_on_slider():
    delivery = await lalamove_service.record_order_delivery(
        _RecordDb(),
        SimpleNamespace(id=uuid.uuid4(), delivery_fee=Decimal("0.00")),
        zone=_zone(),
        cart=_quoted_cart(),
        provider="slider",
    )
    assert delivery.provider == "slider"


@pytest.mark.asyncio
async def test_an_unstamped_row_falls_back_to_the_zone_provider():
    """No provider passed is the identity for the pilot account and for every
    non-Slider zone: the row takes the zone's own courier."""
    delivery = await lalamove_service.record_order_delivery(
        _RecordDb(),
        SimpleNamespace(id=uuid.uuid4(), delivery_fee=Decimal("10.00")),
        zone=_zone(provider="lalamove", name="Dubai Near"),
        cart=_quoted_cart(),
    )
    assert delivery.provider == "lalamove"
