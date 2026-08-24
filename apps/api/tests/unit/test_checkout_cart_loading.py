"""
The checkout's basket has to arrive with everything the checkout reads.

`test_pos_order_list_query` guards the neighbouring failure — a loader naming a
relationship that does not exist — and it would not have caught this one. Every
loader on the checkout's cart compiled perfectly. The bug was a loader that was
**not there**: `availability_service.blocking_groups` walks
`product_modifiers → modifier → options`, `_priceable_cart_options` loaded the
product and its category for pricing and stopped, and the walk lazy-loaded.

Under async SQLAlchemy a lazy load is not a slow query, it is a
`MissingGreenlet` — so both `preview_order` and step 5a of `create_order`
answered 500. A customer with a required-modifier product in the basket could
not see a total and could not place the order.

**And it only ever fired at a branch with something out of stock.**
`unavailable_cart_lines` returns before the per-line loop when nothing is
unavailable, which is the ordinary case on an ordinary day and the case every
test set up. So the checkout was broken on exactly the days the shop had run
out of something, and green everywhere else.

Needs no database. What is asserted is the shape of the load, which is the
thing that was wrong.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.cart import Cart
from app.models.product import Product
from app.services.catalog import availability_service
from app.services.orders.order_service import _priceable_cart_options


def _loaded_paths(options) -> set[str]:
    """
    Every relationship each loader option reaches, as `Parent.attr` strings.

    Read off the real `Load` objects rather than re-derived from the source, so
    a loader that stops one hop short shows up as a missing entry here — which
    is precisely what happened.
    """
    reached: set[str] = set()
    for option in options:
        for element in getattr(option, "context", ()) or ():
            path = getattr(element, "path", None)
            natural = getattr(path, "natural_path", path)
            if not natural:
                continue
            for entry in natural:
                text = str(entry)
                # Path entries alternate `Mapper[X(table)]` and `X.attr`; only
                # the second kind names a relationship that was loaded.
                if "." in text and not text.startswith("Mapper["):
                    reached.add(text)
    return reached


#: What `blocking_groups` walks, in the order it walks it. Spelled out here
#: rather than imported so that changing the walk without changing this is a
#: failing test rather than a silent regression on a stockout day.
BLOCKING_GROUPS_WALK = {
    "CartItem.product",
    "Product.product_modifiers",
    "ProductModifier.modifier",
    "Modifier.options",
}


def test_the_checkout_cart_carries_what_the_stockout_check_walks():
    reached = _loaded_paths(_priceable_cart_options())
    missing = BLOCKING_GROUPS_WALK - reached
    assert not missing, (
        "the checkout's cart does not eager-load "
        f"{sorted(missing)} — `blocking_groups` will lazy-load it and raise "
        "MissingGreenlet, but only at a branch with something out of stock"
    )


def test_the_checkout_cart_still_carries_what_pricing_reads():
    # The load this function existed for before the stockout walk was added to
    # it. Adding a requirement must not quietly drop the original one.
    reached = _loaded_paths(_priceable_cart_options())
    assert {"Cart.items", "CartItem.product", "Product.category"} <= reached


def test_the_two_spellings_of_the_requirement_agree():
    """
    `products_load_options` and `cart_load_options` describe one walk from two
    roots. If they ever disagree, one caller is loading something the other is
    not and the bug comes back at whichever root was forgotten.
    """
    from_product = _loaded_paths(availability_service.products_load_options())
    from_cart = _loaded_paths(availability_service.cart_load_options())

    walk_only = BLOCKING_GROUPS_WALK - {"CartItem.product"}
    assert walk_only <= from_product
    assert walk_only <= from_cart


def test_the_statements_actually_compile():
    """
    The neighbouring guard, kept here too: a loader naming a relationship that
    does not exist raises only when the mapper resolves it, at request time.
    """
    assert str(select(Cart).options(*_priceable_cart_options()))
    assert str(select(Product).options(*availability_service.products_load_options()))


def test_a_loader_that_stops_short_is_visibly_missing():
    """
    The guard above is only worth having if it really can tell a short load
    from a complete one. Asserting the failure mode keeps the test honest.
    """
    short = [selectinload(Cart.items)]
    assert BLOCKING_GROUPS_WALK - _loaded_paths(short)
