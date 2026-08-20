"""
The baskets the shop is holding right now, and whether anyone can be written to.

`tasks/conversion-audit.md` names abandoned-cart recovery as the largest
recoverable revenue line on the site and names the one thing blocking it: a
guest basket carries no address, because the email typed into the checkout was
read to judge a coupon and then dropped. Two things follow from fixing that, and
both are tested here:

* the address is written back to the basket it belongs to — and **only** to a
  guest basket, because an account already has one and a copy would be free to
  go stale;
* `last_activity_at` says when somebody last touched the basket, which
  `updated_at` cannot: adding a line writes `cart_items` and never touches the
  `carts` row at all.

The console screen on top of those is tested through its handler rather than
over HTTP, because what is worth pinning is the arithmetic and the precedence,
not FastAPI's ability to route.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import analytics
from app.models.cart import Cart, CartItem
from app.models.product import Product
from app.models.user import User
from app.services import cart_service

#: Real wall-clock, not a fixed instant.
#:
#: The handler asks the clock itself — a route cannot take a `now` parameter
#: without also offering it as a query string — so the fixtures are written
#: relative to the same clock and the one assertion about a duration allows a
#: minute of slack for the test's own runtime.
NOW = datetime.now(timezone.utc)


# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _product(name="Ferrero Brownie", price="70.00", sku="BRW-01"):
    return Product(id=uuid.uuid4(), name=name, base_price=Decimal(price), sku=sku)


def _item(product, quantity=1, options=None, note=None):
    item = CartItem(
        id=uuid.uuid4(),
        cart_id=uuid.uuid4(),
        product_id=product.id,
        quantity=quantity,
        selected_options=options or [],
        personalisation_note=note,
    )
    item.product = product
    return item


def _cart(
    *,
    items=(),
    user=None,
    guest_email=None,
    session_id="sess-1",
    last_activity_at=NOW,
    promo_code=None,
    quote_fee=None,
    quote_zone=None,
):
    cart = Cart(
        id=uuid.uuid4(),
        user_id=user.id if user else None,
        session_id=None if user else session_id,
        guest_email=guest_email,
        last_activity_at=last_activity_at,
        promo_code=promo_code,
        delivery_quote_fee=quote_fee,
        delivery_quote_zone=quote_zone,
    )
    cart.created_at = NOW - timedelta(hours=3)
    cart.updated_at = last_activity_at or NOW
    cart.user = user
    cart.items = list(items)
    return cart


class _Db:
    """A session that answers one `select` with the carts it was handed."""

    def __init__(self, carts):
        self._carts = carts
        self.flushes = 0

    async def execute(self, _stmt):
        result = MagicMock()
        result.unique.return_value.scalars.return_value.all.return_value = self._carts
        return result

    async def flush(self):
        self.flushes += 1


@pytest.fixture
def settings_row():
    """A 50-dirham small-basket threshold carrying a 15-dirham surcharge."""
    return SimpleNamespace(
        low_order_threshold=Decimal("50.00"),
        low_order_fee=Decimal("15.00"),
        pickup_fee=Decimal("0.00"),
    )


@pytest.fixture(autouse=True)
def _frozen_settings(monkeypatch, settings_row):
    monkeypatch.setattr(
        analytics.delivery_service,
        "get_settings",
        AsyncMock(return_value=settings_row),
    )


async def _live_carts(carts, **kwargs):
    """The handler, called directly — the permission is FastAPI's to enforce."""
    params = {
        "page": 1,
        "per_page": 50,
        "search": None,
        "has_email": None,
        "min_value": None,
        "idle_minutes_min": 0,
        "idle_days_max": 30,
    }
    params.update(kwargs)
    return await analytics.get_live_carts(db=_Db(carts), _admin=None, **params)


# ─── Line pricing ─────────────────────────────────────────────────────────────


def test_a_line_costs_the_product_plus_what_was_chosen_on_it():
    product = _product(price="70.00")
    item = _item(
        product,
        quantity=2,
        options=[
            {"option_name": "Ferrero", "quantity": 2, "charged_total": 10.0},
            {"option_name": "Candles", "quantity": 1, "charged_total": 5.0},
        ],
    )

    assert cart_service.line_unit_price(item) == Decimal("85.00")
    assert cart_service.line_total(item) == Decimal("170.00")


def test_a_line_whose_product_has_gone_prices_at_zero_rather_than_raising():
    """
    `_discard_hidden_items` is what removes such a line. A total is not the place
    to discover one is still there — and a 500 on the basket page would be.
    """
    item = _item(_product(), quantity=1)
    item.product = None

    assert cart_service.line_unit_price(item) == Decimal("0")


def test_the_basket_subtotal_is_the_sum_of_its_lines():
    cart = _cart(
        items=[
            _item(_product(price="70.00"), quantity=2),
            _item(_product(price="25.50"), quantity=1),
        ]
    )

    assert cart_service.subtotal_of(cart) == Decimal("165.50")


# ─── The activity stamp ───────────────────────────────────────────────────────


def test_touching_a_basket_stamps_it():
    cart = _cart(last_activity_at=None)

    assert cart_service.touch(cart, now=NOW) is True
    assert cart.last_activity_at == NOW


def test_a_second_touch_within_the_minute_does_not_write_again():
    """
    `GET /cart` fires on every storefront page load. Without the throttle this
    column is one `UPDATE carts` per page view for every shopper on the site,
    to record something nothing reads to a finer grain than minutes.
    """
    cart = _cart(last_activity_at=NOW)

    assert cart_service.touch(cart, now=NOW + timedelta(seconds=30)) is False
    assert cart.last_activity_at == NOW


def test_a_touch_after_the_minute_does_write_again():
    cart = _cart(last_activity_at=NOW)

    assert cart_service.touch(cart, now=NOW + timedelta(minutes=5)) is True
    assert cart.last_activity_at == NOW + timedelta(minutes=5)


def test_a_naive_stamp_is_replaced_rather_than_compared():
    """
    Subtracting a naive datetime from an aware one raises, and the honest answer
    to "when was this last touched" is never worth a 500 on the basket page.
    """
    cart = _cart(last_activity_at=NOW.replace(tzinfo=None))

    assert cart_service.touch(cart, now=NOW) is True
    assert cart.last_activity_at == NOW


# ─── Writing the checkout's email back ────────────────────────────────────────


async def test_a_guest_basket_remembers_the_address_typed_at_checkout():
    db = _Db([])
    cart = _cart()

    await cart_service.remember_checkout_email(db, cart, "  Someone@Example.COM ")

    assert cart.guest_email == "someone@example.com"
    assert db.flushes == 1


async def test_an_account_basket_never_takes_a_copy_of_the_address():
    """
    It already has one, on `users.email`. A second copy here would be free to go
    stale the day somebody changes their account email — and this column is the
    thing a recovery email would be addressed to.
    """
    db = _Db([])
    user = User(id=uuid.uuid4(), email="member@example.com")
    cart = _cart(user=user)

    await cart_service.remember_checkout_email(db, cart, "typed@example.com")

    assert cart.guest_email is None
    assert db.flushes == 0


async def test_an_unchanged_address_is_still_activity_but_not_a_write():
    """
    Somebody is on the checkout right now, which is the most interesting thing a
    basket can be doing — but the address has not moved, so neither has the row.
    """
    db = _Db([])
    cart = _cart(guest_email="someone@example.com", last_activity_at=None)

    await cart_service.remember_checkout_email(db, cart, "someone@example.com")

    assert cart.guest_email == "someone@example.com"
    assert cart.last_activity_at is not None


async def test_an_empty_address_changes_nothing():
    db = _Db([])
    cart = _cart(guest_email="someone@example.com")

    await cart_service.remember_checkout_email(db, cart, "   ")

    assert cart.guest_email == "someone@example.com"
    assert db.flushes == 0


async def test_a_basket_that_does_not_exist_is_not_an_error():
    """A preview fires while the basket is still loading. That is ordinary."""
    await cart_service.remember_checkout_email(_Db([]), None, "someone@example.com")


# ─── Which address the console shows ──────────────────────────────────────────


def test_the_account_address_wins_over_a_stored_guest_one():
    """
    They cannot both be set by anything running today — `guest_email` is only
    written for a basket without a `user_id`. The precedence is stated so a row
    restored from a dump that predates that rule still answers with the address
    that cannot be stale.
    """
    user = User(id=uuid.uuid4(), email="member@example.com")
    cart = _cart(user=user)
    cart.guest_email = "typed@example.com"

    assert analytics._cart_email(cart) == ("member@example.com", "account")


def test_a_guest_basket_answers_with_what_was_typed_at_checkout():
    cart = _cart(guest_email="typed@example.com")

    assert analytics._cart_email(cart) == ("typed@example.com", "checkout")


def test_a_basket_nobody_can_be_written_to_says_so():
    assert analytics._cart_email(_cart()) == (None, None)


def test_options_read_as_words():
    assert (
        analytics._option_words(
            {"modifier_name": "Filling", "option_name": "Ferrero", "quantity": 2}
        )
        == "Filling: Ferrero ×2"
    )
    # A quantity of one is the ordinary case and saying "×1" is noise.
    assert analytics._option_words({"option_name": "Ferrero", "quantity": 1}) == (
        "Ferrero"
    )


# ─── The screen ───────────────────────────────────────────────────────────────


async def test_a_live_basket_carries_its_value_its_fees_and_its_promo():
    cart = _cart(
        items=[_item(_product(price="70.00"), quantity=2)],
        guest_email="typed@example.com",
        promo_code="NEW",
        quote_fee=Decimal("18.00"),
        quote_zone="Dubai Marina",
        last_activity_at=NOW - timedelta(hours=2),
    )

    result = await _live_carts([cart])

    (row,) = result.items
    assert row.subtotal == 140.0
    # Above the 50-dirham threshold, so no small-basket surcharge.
    assert row.low_order_fee == 0.0
    assert row.delivery_fee == 18.0
    assert row.delivery_zone == "Dubai Marina"
    assert row.estimated_total == 158.0
    assert row.promo_code == "NEW"
    assert row.email == "typed@example.com"
    assert row.email_source == "checkout"
    assert row.item_count == 2
    assert 119 <= row.idle_minutes <= 121


async def test_a_small_basket_shows_the_surcharge_it_would_attract():
    """
    From `order_pricing.low_order_fee_for` — the same function that charges it.
    A second copy of the threshold rule on this screen is how the console ends
    up quoting a fee the checkout does not.
    """
    cart = _cart(items=[_item(_product(price="30.00"))])

    result = await _live_carts([cart])

    (row,) = result.items
    assert row.subtotal == 30.0
    assert row.low_order_fee == 15.0
    assert row.estimated_total == 45.0


async def test_a_basket_with_no_pin_quotes_no_delivery_fee():
    """
    There is no honest fee for an address nobody has given yet, and inventing
    one puts a number on this screen the shop never quoted.
    """
    cart = _cart(items=[_item(_product(price="70.00"))])

    (row,) = (await _live_carts([cart])).items
    assert row.delivery_fee is None
    assert row.estimated_total == 70.0


async def test_the_estimate_ignores_the_promo_code_on_the_basket():
    """
    The code, never the discount (migration 115). What a coupon is worth depends
    on the basket, the identity and the day; a figure quoted here would be a
    second answer, from a screen that cannot see the first.
    """
    cart = _cart(items=[_item(_product(price="70.00"))], promo_code="NEW")

    (row,) = (await _live_carts([cart])).items
    assert row.promo_code == "NEW"
    assert row.estimated_total == 70.0


async def test_the_summary_states_the_recovery_case():
    reachable = _cart(
        items=[_item(_product(price="70.00"), quantity=2)],
        guest_email="typed@example.com",
        last_activity_at=NOW - timedelta(hours=2),
    )
    unreachable = _cart(
        items=[_item(_product(price="30.00"))],
        last_activity_at=NOW - timedelta(minutes=5),
    )
    cold = _cart(
        items=[_item(_product(price="100.00"))],
        user=User(id=uuid.uuid4(), email="member@example.com"),
        last_activity_at=NOW - timedelta(days=2),
    )

    summary = (await _live_carts([reachable, unreachable, cold])).summary

    assert summary.carts == 3
    assert summary.total_value == 270.0
    assert summary.with_email == 2
    assert summary.reachable_value == 240.0
    assert summary.idle_over_1h == 2
    assert summary.idle_over_24h == 1


async def test_only_baskets_worth_the_floor_are_listed():
    small = _cart(items=[_item(_product(price="30.00"))])
    big = _cart(items=[_item(_product(price="200.00"))])

    result = await _live_carts([small, big], min_value=100)

    assert [row.subtotal for row in result.items] == [200.0]
    # The header follows the filter — comparing "reachable" against "sitting
    # there" is only a comparison if both describe the same set.
    assert result.summary.carts == 1


async def test_the_page_is_a_slice_and_the_total_is_not():
    carts = [_cart(items=[_item(_product(price="70.00"))]) for _ in range(5)]

    result = await _live_carts(carts, page=2, per_page=2)

    assert result.total == 5
    assert result.pages == 3
    assert len(result.items) == 2


async def test_an_account_basket_is_marked_registered_and_a_guest_account_is_not():
    member = _cart(
        items=[_item(_product())],
        user=User(id=uuid.uuid4(), email="member@example.com", is_guest=False),
    )
    guest_account = _cart(
        items=[_item(_product())],
        user=User(id=uuid.uuid4(), email="guest@example.com", is_guest=True),
    )

    rows = (await _live_carts([member, guest_account])).items

    assert [row.is_registered for row in rows] == [True, False]
    # Both are reachable, and both addresses came off the account row.
    assert [row.email_source for row in rows] == ["account", "account"]


# ─── The query itself ─────────────────────────────────────────────────────────
#
# Compiled against PostgreSQL rather than exercised through a session, because
# the tests above hand the handler a list and never touch a database — so
# nothing else here would notice a statement that does not build. Everything in
# it is the kind of thing that is fine in Python and wrong in SQL.


def _sql(**kwargs) -> str:
    from sqlalchemy.dialects import postgresql

    stmt = analytics.live_carts_query(NOW, **kwargs)
    return str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_the_query_asks_for_baskets_that_hold_something():
    sql = _sql()

    assert "EXISTS" in sql and "cart_items" in sql
    # One outer join to `users`, not two: `contains_eager` populates `cart.user`
    # from the join the filters already needed, where a `joinedload` would add
    # an aliased second copy.
    assert sql.count("LEFT OUTER JOIN users") == 1
    assert "NULLS LAST" in sql


def test_the_reachable_filter_covers_both_places_an_address_can_live():
    sql = _sql(has_email=True)

    assert "users.email IS NOT NULL" in sql
    assert "carts.guest_email IS NOT NULL" in sql


def test_the_unreachable_filter_requires_neither():
    sql = _sql(has_email=False)

    assert "users.email IS NULL" in sql
    assert "carts.guest_email IS NULL" in sql


def test_search_reaches_the_session_id_as_well_as_the_addresses():
    """A basket with no email is found by the only handle it has."""
    sql = _sql(search="alice")

    assert "carts.session_id ILIKE" in sql
    assert "users.email ILIKE" in sql
    assert "carts.guest_email ILIKE" in sql


def test_the_window_is_bounded_at_both_ends():
    """
    `carts` has no expiry. Without the far end this grows to every basket ever
    abandoned, and the page nobody wants is the one the database works hardest
    for.
    """
    sql = _sql(idle_minutes_min=60, idle_days_max=7)

    assert str((NOW - timedelta(days=7)).date()) in sql
    assert ">=" in sql and "<=" in sql
