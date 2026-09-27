"""
The listing and the product page have to agree about what exists.

Nine cakes were marked out at both branches and went on appearing on category
pages and in search, because `get_all` spelled the web predicate out inline —
`sells_on` and the category clause, and not the availability half that
`website_product_visibility_clause` carries. `get_by_slug` uses the shared
clause, so the same nine answered 404 when clicked.

On the category page, gone when clicked, is worse than either answer on its own.

There is no database in this suite, so these read the SQL the query builds.
That is the right level for this bug anyway: it was never about what the rows
say, it was about which predicate reached the statement at all.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.catalog import product_service


async def _sql_for(**kwargs) -> str:
    """Every statement `get_all` runs, as text."""
    statements: list[str] = []

    async def execute(statement):
        statements.append(
            str(statement.compile(compile_kwargs={"literal_binds": False}))
        )
        result = MagicMock()
        result.scalar.return_value = 0
        result.scalars.return_value.unique.return_value.all.return_value = []
        return result

    db = AsyncMock()
    db.execute = execute
    await product_service.get_all(db, **kwargs)
    return "\n".join(statements)


async def test_a_shopper_is_not_offered_what_no_branch_can_make():
    sql = await _sql_for(channel="web")

    assert "branch_products" in sql, (
        "the listing is not applying branch availability — the state that put "
        "nine sold-out cakes on the category page and a 404 behind each one"
    )


async def test_the_count_and_the_page_are_filtered_the_same_way():
    """
    Both statements come off one `stmt`, and a filter added to only one would
    paginate a catalogue of a different size than it lists.
    """
    sql = await _sql_for(channel="web")

    assert sql.count("branch_products") >= 2, "count and page must agree"


async def test_staff_still_see_what_the_shop_has_marked_out():
    """
    The console is where a product is put back. Hiding it there is how it never
    is — and the same endpoint serves both, so this is the line between them.
    """
    sql = await _sql_for(channel="web", staff=True, include_inactive=True)

    assert "branch_products" not in sql


async def test_the_register_keeps_its_own_catalogue():
    """
    The terminal has its own availability screen and its own per-branch answer;
    the storefront's "out at every branch" rule is not the question it asks.
    """
    sql = await _sql_for(channel="pos", staff=True)

    assert "branch_products" not in sql


async def test_the_pin_union_counts_only_online_branches():
    """
    Narrowing to a pin's serving branches (`branch_ids`) must count the same
    online-eligible set the checkout walks. A branch switched off for online
    orders is not a kitchen this order can go to, so its shelf must not keep a
    product on the storefront that the checkout would then refuse — the
    "promise nobody can keep". The set predicate must therefore constrain on
    `receives_online_orders`, exactly as the estate-wide union does.
    """
    import uuid

    sql = await _sql_for(channel="web", branch_ids=[uuid.uuid4(), uuid.uuid4()])

    assert "branch_products" in sql, "the pin union still applies availability"
    assert "receives_online_orders" in sql, (
        "the pin's serving set must exclude branches switched off for online "
        "orders, or the catalogue lists what the checkout refuses"
    )


def test_estate_union_falls_back_when_no_branch_is_assigned():
    """
    The estate-wide union counts branches assigned on the active map. If a map
    is ever published with no assignments at all, requiring an assignment would
    empty the set and the count-trick's `branch_count > 0` guard would silently
    show the whole catalogue unfiltered. The predicate must therefore carry a
    `NOT EXISTS` fallback to all online branches so stock filtering survives a
    map with no assignments.
    """
    from app.services.catalog import availability_service

    sql = str(availability_service.out_at_every_branch_subquery())

    assert "EXISTS" in sql.upper(), (
        "the estate union lost its empty-map fallback — a map with no "
        "assignments would show every product unfiltered"
    )


def test_pin_union_falls_back_to_the_estate_when_its_branches_are_all_closed():
    """
    When every branch serving a pin is switched off, the pin's own set is empty
    and the count-trick would show the whole catalogue. The set predicate must
    fall back to the estate-wide online union (itself stock-filtered) rather than
    dropping the filter — so an unserviceable pin never yields an unfiltered
    catalogue.
    """
    import uuid

    from app.services.catalog import availability_service

    sql = str(availability_service.out_at_every_branch_in_set_subquery([uuid.uuid4()]))

    # The fallback disjunct pulls in the assignment-driven estate set, whose join
    # to the version table only appears when the estate union is spliced in.
    assert "EXISTS" in sql.upper(), "the pin union lost its all-closed fallback"
    assert "delivery_polygon_versions" in sql, (
        "the all-closed fallback must widen to the estate union, which is the "
        "only path that joins the active-map version"
    )


# ── The product page is a page, sold out or not ─────────────────────────────
#
# The listing hides a product no kitchen can make; the product page used to
# 404 it as well. Cakes sell out most evenings, so every sell-out dropped the
# page from the search index and broke every link to it until the morning.
# The page now resolves and says `is_available=False`; only a product that is
# genuinely gone is a 404.


class _Response:
    """Stands in for `ProductResponse` — these tests are about the queries."""

    @classmethod
    def model_validate(cls, _product):
        return cls()

    def model_copy(self, *, update):
        return update


def _db_with_product(availability_answer):
    """A session whose lookup finds a product, and whose availability check
    answers `availability_answer`. Records every statement either one ran."""
    lookups: list[str] = []
    checks: list[str] = []

    async def execute(statement):
        lookups.append(str(statement))
        result = MagicMock()
        result.scalar_one_or_none.return_value = MagicMock(id="p-1")
        return result

    async def scalar(statement):
        checks.append(str(statement))
        return availability_answer

    db = AsyncMock()
    db.execute = execute
    db.scalar = scalar
    return db, lookups, checks


async def test_a_sold_out_product_page_is_found_and_says_so(monkeypatch):
    monkeypatch.setattr(product_service, "ProductResponse", _Response)
    db, lookups, checks = _db_with_product(None)

    response = await product_service.get_by_slug(db, "tiramisu")

    assert response == {"is_available": False}
    assert "branch_products" not in lookups[0], (
        "the page lookup filters on stock again — a sold-out cake is a 404, "
        "and falls out of the search index every evening"
    )
    assert "branch_products" in checks[0], "availability is no longer checked"


async def test_an_in_stock_product_page_says_it_can_be_bought(monkeypatch):
    monkeypatch.setattr(product_service, "ProductResponse", _Response)
    db, _, _ = _db_with_product("p-1")

    assert await product_service.get_by_slug(db, "tiramisu") == {"is_available": True}


async def test_a_retired_product_is_still_a_404(monkeypatch):
    """Deactivated, off the web or in a retired category is gone, not sold out."""
    import pytest

    from app.core.exceptions import NotFoundError

    async def execute(statement):
        sql = str(statement)
        assert "products.is_active" in sql and "sales_channels" in sql
        assert "categories" in sql
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    db = AsyncMock()
    db.execute = execute
    with pytest.raises(NotFoundError):
        await product_service.get_by_slug(db, "discontinued")


async def test_the_sitemap_can_list_every_page_but_the_shop_cannot():
    """`include_unavailable` drops only the stock half, never the page rules."""
    sitemap = await _sql_for(channel="web", include_unavailable=True)
    shop = await _sql_for(channel="web")

    assert "branch_products" not in sitemap
    assert "sales_channels" in sitemap and "products.is_active" in sitemap
    assert "branch_products" in shop
