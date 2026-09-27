"""
The abandoned-cart sweep: which link each reminder carries, and that nobody is
mailed twice.

Two kinds of abandonment reach the sweep — an unpaid `created` order and a basket
that never became one — and a customer can leave both. The rules pinned here:

- An unpaid order links to the gateway's live payment page when it has one, and
  to its own retry screen on the checkout when it does not (an in-page Apple Pay
  attempt has no hosted page — MM-20260927-001), but never when the money has in
  fact moved.
- A basket links to the checkout.
- One reminder per address, whichever kind, with orders taking precedence.
- Nothing abandoned before the launch floor.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import email_service
from app.services.orders import abandoned_checkout_service as acs

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _order(email="buyer@example.com", **over):
    base = dict(
        id=uuid.uuid4(),
        order_number=f"MM-{uuid.uuid4().hex[:6]}",
        email=email,
        locale="en",
        payment_provider="stripe",
        payment_id="",
        payment_transactions=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


def _basket(email="buyer@example.com", **over):
    base = dict(
        cart_id=uuid.uuid4(),
        email=email,
        last_added_at=NOW - timedelta(hours=3),
        items=[{"name": "Tiramisu", "quantity": 1}],
        subtotal=Decimal("30.00"),
    )
    base.update(over)
    return acs._Basket(**base)


@pytest.fixture
def sweep(monkeypatch):
    """Everything around `sweep_once` stubbed: the lock, the session, the
    candidate reads, the journal and the two senders. Tests set the candidates
    and the gateway, then read what was sent."""

    @asynccontextmanager
    async def _mine(*_a, **_k):
        yield True

    @asynccontextmanager
    async def _session():
        yield None

    state = SimpleNamespace(
        orders=[],
        baskets=[],
        resume_url=None,
        paid=False,
        recently=set(),
        already=set(),
        refs=set(),
    )

    monkeypatch.setattr(acs.settings, "ABANDONED_CART_EMAIL_ENABLED", True)
    monkeypatch.setattr(acs.advisory_lock, "held", _mine)
    monkeypatch.setattr(acs, "AsyncSessionFactory", _session)
    monkeypatch.setattr(
        acs, "_order_candidates", AsyncMock(side_effect=lambda db, now: state.orders)
    )
    monkeypatch.setattr(
        acs, "_basket_candidates", AsyncMock(side_effect=lambda db, now: state.baskets)
    )
    monkeypatch.setattr(
        acs.order_service, "to_response", AsyncMock(side_effect=lambda db, o: o)
    )
    monkeypatch.setattr(
        email_service,
        "already_sent",
        AsyncMock(side_effect=lambda number, _t: number in state.already),
    )
    monkeypatch.setattr(
        email_service,
        "reference_sent",
        AsyncMock(side_effect=lambda ref, _t: ref in state.refs),
    )
    monkeypatch.setattr(
        email_service,
        "sent_to_recently",
        AsyncMock(side_effect=lambda email, _t, since: email in state.recently),
    )
    state.order_mail = AsyncMock()
    state.basket_mail = AsyncMock()
    monkeypatch.setattr(email_service, "send_abandoned_cart", state.order_mail)
    monkeypatch.setattr(email_service, "send_abandoned_basket", state.basket_mail)
    monkeypatch.setattr(
        acs,
        "PROVIDERS",
        {
            "stripe": SimpleNamespace(
                resume_url=AsyncMock(side_effect=lambda o: state.resume_url)
            )
        },
    )
    monkeypatch.setattr(acs.payment_service, "_is_paid", lambda o: state.paid)
    monkeypatch.setattr(
        acs.payment_service,
        "_gateway_reports_paid",
        AsyncMock(side_effect=lambda o: False),
    )
    monkeypatch.setattr(acs.settings, "WEB_URL", "https://shop.example")
    return state


# ── unpaid orders ─────────────────────────────────────────────────────────────


async def test_an_order_with_a_live_session_links_to_the_gateway(sweep):
    sweep.orders = [_order()]
    sweep.resume_url = "https://checkout.stripe.com/c/pay/cs_live_x"

    assert await acs.sweep_once(now=NOW) == 1
    assert sweep.order_mail.await_args.kwargs["resume_url"] == sweep.resume_url


async def test_an_order_without_a_session_links_to_its_retry_screen(sweep):
    """An in-page Apple Pay attempt keeps a PaymentIntent, not a Checkout
    Session, so there is no hosted page to resume — the checkout's own retry
    screen for that order is the way back."""
    order = _order(locale="ar")
    sweep.orders = [order]

    assert await acs.sweep_once(now=NOW) == 1
    assert sweep.order_mail.await_args.kwargs["resume_url"] == (
        "https://shop.example/ar/checkout"
        f"?step=payment&order_number={order.order_number}"
    )


async def test_an_order_that_was_actually_paid_is_never_told_to_pay(sweep):
    """A missed success webhook leaves a paid order at `created` too."""
    sweep.orders = [_order()]
    sweep.paid = True

    assert await acs.sweep_once(now=NOW) == 0
    sweep.order_mail.assert_not_awaited()


async def test_an_order_already_reminded_is_not_reminded_again(sweep):
    order = _order()
    sweep.orders = [order]
    sweep.already = {order.order_number}

    assert await acs.sweep_once(now=NOW) == 0


async def test_a_guest_placeholder_address_is_never_mailed(sweep):
    sweep.orders = [_order(email="guest-1a2b3c4d@guest.local")]

    assert await acs.sweep_once(now=NOW) == 0


# ── baskets ───────────────────────────────────────────────────────────────────


async def test_a_basket_links_to_the_checkout(sweep):
    basket = _basket()
    sweep.baskets = [basket]

    assert await acs.sweep_once(now=NOW) == 1
    kwargs = sweep.basket_mail.await_args.kwargs
    assert kwargs["to"] == "buyer@example.com"
    assert kwargs["checkout_url"] == "https://shop.example/en/checkout"
    assert kwargs["reference"] == basket.reference


async def test_a_basket_episode_already_reminded_is_skipped(sweep):
    basket = _basket()
    sweep.baskets = [basket]
    sweep.refs = {basket.reference}

    assert await acs.sweep_once(now=NOW) == 0


def test_a_basket_that_gains_a_line_is_a_new_episode():
    cart_id = uuid.uuid4()
    first = _basket(cart_id=cart_id)
    later = _basket(
        cart_id=cart_id, last_added_at=first.last_added_at + timedelta(days=2)
    )

    assert first.reference != later.reference
    assert len(later.reference) <= 64  # fits `email_logs.reference`


# ── never twice ───────────────────────────────────────────────────────────────


async def test_an_order_and_a_basket_for_one_address_send_one_email(sweep):
    """A live Stripe session and a re-filled basket for the same customer: the
    order's payment link goes, the basket is held back."""
    sweep.orders = [_order()]
    sweep.baskets = [_basket()]
    sweep.resume_url = "https://checkout.stripe.com/c/pay/cs_live_x"

    assert await acs.sweep_once(now=NOW) == 1
    sweep.order_mail.assert_awaited_once()
    sweep.basket_mail.assert_not_awaited()


async def test_two_unpaid_orders_for_one_address_send_one_email(sweep):
    sweep.orders = [_order(), _order()]
    sweep.resume_url = "https://checkout.stripe.com/c/pay/cs_live_x"

    assert await acs.sweep_once(now=NOW) == 1


async def test_an_address_reminded_in_the_last_day_is_left_alone(sweep):
    """The same guard across ticks, read from the journal."""
    sweep.orders = [_order()]
    sweep.baskets = [_basket()]
    sweep.recently = {"buyer@example.com"}

    assert await acs.sweep_once(now=NOW) == 0


async def test_different_addresses_are_each_reminded(sweep):
    sweep.baskets = [_basket(email="a@example.com"), _basket(email="b@example.com")]

    assert await acs.sweep_once(now=NOW) == 2


# ── the window ────────────────────────────────────────────────────────────────


def test_nothing_before_the_launch_floor_is_ever_due(monkeypatch):
    """The first tick after deploy must not reach back into weeks of baskets."""
    monkeypatch.setattr(acs.settings, "ABANDONED_CART_MAX_AGE_HOURS", 23)
    monkeypatch.setattr(acs.settings, "ABANDONED_CART_AFTER_MINUTES", 120)
    just_after_launch = acs._START_AT + timedelta(hours=5)

    oldest, newest = acs._window(just_after_launch)

    assert oldest == acs._START_AT
    assert newest == just_after_launch - timedelta(hours=2)


def test_the_window_opens_two_hours_after_the_last_add(monkeypatch):
    monkeypatch.setattr(acs.settings, "ABANDONED_CART_MAX_AGE_HOURS", 23)
    monkeypatch.setattr(acs.settings, "ABANDONED_CART_AFTER_MINUTES", 120)

    oldest, newest = acs._window(NOW)

    assert newest == NOW - timedelta(hours=2)
    assert oldest == NOW - timedelta(hours=23)


@pytest.mark.parametrize(
    "address, expected",
    [
        ("  Buyer@Example.com ", "buyer@example.com"),
        ("guest-1a2b3c4d@guest.local", None),
        ("", None),
        (None, None),
        ("not-an-address", None),
    ],
)
def test_real_email(address, expected):
    assert acs._real_email(address) == expected


# ── the basket email itself ───────────────────────────────────────────────────


async def test_the_basket_email_renders_and_is_journalled(monkeypatch):
    sent = AsyncMock(return_value={"status": "sent", "resend_id": "r1", "error": None})
    logged = AsyncMock()
    monkeypatch.setattr(email_service, "_send_async", sent)
    monkeypatch.setattr(email_service, "_log", logged)

    await email_service.send_abandoned_basket(
        to="buyer@example.com",
        items=[
            {
                "name": "Tiramisu",
                "options": "",
                "quantity": 2,
                "unit_price": "30.00",
                "total_price": "60.00",
                "note": None,
            }
        ],
        subtotal=Decimal("60"),
        checkout_url="https://shop.example/en/checkout",
        reference="cart:abc:123",
    )

    to, subject, html = sent.await_args.args
    assert to == "buyer@example.com"
    assert "Tiramisu" in html
    assert "https://shop.example/en/checkout" in html
    assert "AED 60.00" in html
    assert logged.await_args.args[0] == "abandoned_basket"
    assert logged.await_args.kwargs["reference"] == "cart:abc:123"


# ── the basket query (DB-gated) ───────────────────────────────────────────────

_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(
    not _DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)
async def test_basket_candidates_reads_the_real_address_and_the_last_add():
    """Against Postgres, because the selection is a grouped subquery: which
    baskets are due, which address each is written to, and that a guest
    account's placeholder gives way to the typed address."""
    from sqlalchemy import delete as sql_delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.base import utcnow
    from app.models.cart import Cart, CartItem
    from app.models.product import Product
    from app.models.user import User

    engine = create_async_engine(_DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    now = utcnow()
    tag = uuid.uuid4().hex[:8]

    async with Session() as s:
        product = Product(name="Tiramisu", slug=f"tiramisu-{tag}", base_price=30)
        member = User(email=f"member-{tag}@example.com", is_guest=False)
        guest = User(email=f"guest-{tag}@guest.local", is_guest=True)
        silent = User(email=f"silent-{tag}@guest.local", is_guest=True)
        s.add_all([product, member, guest, silent])
        await s.flush()

        def cart(user, *, guest_email=None, minutes_old):
            row = Cart(user_id=user.id, guest_email=guest_email)
            s.add(row)
            return row, minutes_old

        seeded = [
            cart(member, minutes_old=150),  # due, account address
            cart(guest, guest_email=f"typed-{tag}@example.com", minutes_old=150),
            cart(silent, minutes_old=150),  # due, but nobody to write to
        ]
        await s.flush()
        for row, minutes_old in seeded:
            s.add(
                CartItem(
                    cart_id=row.id,
                    product_id=product.id,
                    quantity=2,
                    created_at=now - timedelta(minutes=minutes_old),
                )
            )
        # A member basket with a fresh line on top of an old one: not due yet.
        fresh = Cart(user_id=(await _user(s, f"fresh-{tag}@example.com")).id)
        s.add(fresh)
        await s.flush()
        s.add_all(
            [
                CartItem(
                    cart_id=fresh.id,
                    product_id=product.id,
                    created_at=now - timedelta(minutes=300),
                ),
                CartItem(
                    cart_id=fresh.id,
                    product_id=product.id,
                    created_at=now - timedelta(minutes=10),
                ),
            ]
        )
        await s.commit()
    carts = [row.id for row, _ in seeded] + [fresh.id]

    try:
        async with Session() as s:
            found = {
                b.cart_id: b
                for b in await acs._basket_candidates(s, now=now)
                if b.cart_id in carts
            }
        assert {b.email for b in found.values()} == {
            f"member-{tag}@example.com",
            f"typed-{tag}@example.com",
        }
        member_basket = found[seeded[0][0].id]
        assert member_basket.subtotal == Decimal("60")
        assert member_basket.items[0]["name"] == "Tiramisu"
        assert fresh.id not in found
    finally:
        async with Session() as s:
            await s.execute(sql_delete(CartItem).where(CartItem.cart_id.in_(carts)))
            await s.execute(sql_delete(Cart).where(Cart.id.in_(carts)))
            await s.execute(sql_delete(User).where(User.email.like(f"%{tag}%")))
            await s.execute(
                sql_delete(Product).where(Product.slug == f"tiramisu-{tag}")
            )
            await s.commit()
        await engine.dispose()


async def _user(s, email):
    from app.models.user import User

    user = User(email=email, is_guest=False)
    s.add(user)
    await s.flush()
    return user
