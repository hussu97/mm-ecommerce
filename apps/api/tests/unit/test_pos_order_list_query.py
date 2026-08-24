"""
The register's order list has to compile against the real mapper.

`GET /pos/orders` feeds two things that look unrelated and are not: the order
history a cashier reads, and the poll behind the incoming-order queue — which is
what accepts orders, prints them and now books the courier. When that endpoint
500s, the shop loses its history *and* silently stops taking website orders.

That happened on 2026-08-15. `selectinload(Order.branch)` was added for the
opening-hours rule and `Order` had no `branch` relationship. It is not a syntax
error and nothing imports it eagerly: SQLAlchemy resolves the attribute against
the mapper when the option is applied, so it raised `type object 'Order' has no
attribute 'branch'` at request time and nowhere else. The whole suite was green.
MM-20260815-002 landed fourteen minutes later, was never accepted, and no driver
was ever called for it.

Needs no database. Building the statement is what fails.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import selectinload

from app.api.v1 import pos_orders
from app.models.order import Order


def test_every_eager_load_on_the_register_list_resolves():
    # The endpoint's own options, not a copy of them — a copy would keep passing
    # while the endpoint drifted, which is precisely the failure being guarded.
    statement = select(Order).options(*pos_orders._list_load_options())
    # Compiling forces the mapper resolution that request time would.
    assert str(statement)


def test_a_relationship_that_does_not_exist_still_fails_loudly():
    """
    The guard above is only worth having if the thing it catches really is
    catchable this way. Asserting the failure mode keeps the test honest — if a
    future SQLAlchemy defers this resolution further, this goes red and says so
    rather than the other test quietly protecting nothing.
    """
    import pytest

    with pytest.raises(AttributeError):
        select(Order).options(selectinload(Order.kitchen_that_does_not_exist))


def test_the_branch_is_among_them():
    """
    `_serialise` guards on `inspect(order).unloaded`, so a missing eager load
    does not raise — it silently drops `may_auto_accept` from the payload and
    every order reads as auto-acceptable, including the 03:00 ones the rule
    exists to stop. The guard makes the mistake quiet, so the test has to make
    it loud.
    """
    # `path` alternates mapper, attribute, mapper — the attribute at index 1 is
    # the relationship this option loads off `Order`.
    loaded = {opt.path[1].key for opt in pos_orders._list_load_options()}
    assert "branch" in loaded
    assert "delivery" in loaded


def test_the_register_list_is_offset_paged_with_a_stable_order():
    """
    Infinite scroll pages `GET /pos/orders` by offset, so the order has to be
    total — `Order.id` breaks ties `opened_at`/`created_at` leave, or a
    same-instant order lands on two pages (skip) or none (dupe). Asserted against
    the endpoint's own `_ordered_page`, not a copy of it.
    """
    sql = str(
        pos_orders._ordered_page(select(Order), limit=10, offset=25).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "LIMIT 10" in sql
    assert "OFFSET 25" in sql
    # The unique tiebreaker, last in the ORDER BY.
    assert "orders.id DESC" in sql
    assert sql.index("orders.opened_at") < sql.index("orders.id DESC")
