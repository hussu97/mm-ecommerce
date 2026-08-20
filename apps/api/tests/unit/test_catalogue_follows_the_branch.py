"""
What the shopper is shown is what their kitchen can make.

The catalogue used to answer for the estate: a product was hidden only when
*every* active branch had marked it out, on the reasoning that hiding a cake
from somebody whose own shop has it on the shelf is worse than offering one that
needs a different branch. That reasoning holds exactly as far as "we do not know
where they are".

Once the pin has named a kitchen — and `/delivery/area` now hands that kitchen
back — showing what it cannot make is not caution, it is a promise nobody can
keep: the checkout resolves the same branch from the same pin and refuses the
line.

No database here, so these read the SQL the queries build. That is the right
level: the bug this guards against is a predicate not reaching the statement.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

from app.core.exceptions import NotFoundError
from app.services import category_service, product_service


async def _sql_for(fn, *args, **kwargs) -> str:
    statements: list[str] = []

    async def execute(statement):
        statements.append(str(statement))
        result = MagicMock()
        result.scalar.return_value = 0
        result.all.return_value = []
        result.scalar_one_or_none.return_value = None
        result.scalars.return_value.unique.return_value.all.return_value = []
        return result

    db = AsyncMock()
    db.execute = execute
    try:
        await fn(db, *args, **kwargs)
    except NotFoundError:
        # `get_by_slug` finds nothing against an empty mock, which is fine: the
        # statement it built is what these tests read.
        pass
    return "\n".join(statements)


BRANCH = uuid.uuid4()


async def test_a_shopper_with_a_pin_sees_only_their_kitchen():
    sql = await _sql_for(product_service.get_all, channel="web", branch_id=BRANCH)

    assert "branch_products.branch_id" in sql, "not filtered to one branch"
    # The required-group half as well: a box of three whose every filling is out
    # here is not a box, even though nobody marked the box itself.
    assert "product_modifiers" in sql and "branch_modifier_options" in sql


async def test_a_shopper_who_has_told_us_nothing_sees_what_any_branch_can_make():
    """
    A crawler, or a first visit before the browser has resolved a location. The
    widest honest answer, and deliberately so: a page cached for somebody with
    no address must not carry one branch's stockouts.
    """
    sql = await _sql_for(product_service.get_all, channel="web")

    assert "branch_products" in sql
    assert "branch_products.branch_id = " not in sql


async def test_the_product_page_and_the_listing_agree():
    """
    The pair that already drifted once: the listing had its own copy of the web
    predicate and kept nine sold-out cakes on the page that 404'd when clicked.
    Both now take the same branch and answer the same way.
    """
    listing = await _sql_for(product_service.get_all, channel="web", branch_id=BRANCH)
    page = await _sql_for(
        product_service.get_by_slug, "basque-cheesecake", branch_id=BRANCH
    )

    for sql in (listing, page):
        assert "branch_products.branch_id" in sql


async def test_the_nav_counts_against_the_same_branch_as_the_grid():
    """
    A count taken across the estate beside a grid filtered to one kitchen is a
    category header reading "6" above four cakes — and a chip that opens an
    empty page when the branch has nothing in it.
    """
    sql = await _sql_for(category_service.get_all, channel="web", branch_id=BRANCH)

    assert "branch_products.branch_id" in sql


async def test_the_console_is_never_filtered_by_a_branch():
    """
    The list an operator edits is what exists. A product hidden from the screen
    where it is put back is a product that never is.
    """
    sql = await _sql_for(
        product_service.get_all, channel="web", staff=True, branch_id=BRANCH
    )

    assert "branch_products" not in sql
