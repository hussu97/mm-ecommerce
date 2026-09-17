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
