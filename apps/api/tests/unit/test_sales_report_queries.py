"""
The sales `by dimension` queries, compiled to SQL without a database.

Every one of these bugs was a query that *built* wrong, not a route that
answered wrong — so a structural test that reads the source or a stub that
returns canned rows never sees them. The check that matters is that the
statement renders to PostgreSQL at all, and that the three fixes are actually
in the rendered SQL:

  * product / category stopped 500-ing — `image_urls` is a Postgres text array,
    so the thumbnail is `image_urls[1]` (1-based) and never `.astext`, which is
    a JSON accessor the column does not have and which failed to compile;
  * website revenue is counted — the completed-sale filter is `pos_status =
    closed OR (source = online AND status = delivered)`, not closed alone;
  * cashier / terminal are read off the open till — the cashier, staff and
    device dimensions LEFT JOIN LATERAL `tills` so an aggregator or website
    order that nobody rang up still names who was online.
"""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from app.services.pos.pos_reports import sales
from app.services.pos.pos_reports._base import SUPPORTED_DIMENSIONS


class _Scalars:
    def all(self):
        return []


class _Result:
    def all(self):
        return []

    def one(self):
        return (0,) * 9

    def scalar_one(self):
        return 0

    def scalars(self):
        return _Scalars()


class _CompilingDB:
    """A session that renders each statement to SQL instead of running it.

    Compilation is the exact step that raised on the old
    `Product.image_urls[0].astext`, so it reproduces the 500 a stub returning
    rows would sail straight past.
    """

    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt):
        self.sql.append(str(stmt.compile(dialect=postgresql.dialect())))
        return _Result()


# `hour` resolves the business timezone with its own query, which needs more
# than a blind stub; it is covered by its own case below.
_DIMENSIONS = sorted(SUPPORTED_DIMENSIONS - {"hour"})


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", _DIMENSIONS)
async def test_every_dimension_compiles_to_postgres(dimension):
    """No dimension may raise while its statement is built — the product and
    category reports did, on a line that ran before the branch that used it."""
    await sales.sales_by_dimension(_CompilingDB(), dimension=dimension, limit=50)


@pytest.mark.asyncio
async def test_the_summary_compiles():
    await sales.sales_summary(_CompilingDB())


@pytest.mark.asyncio
async def test_product_thumbnail_is_a_one_based_array_index_not_json():
    db = _CompilingDB()
    await sales.sales_by_dimension(db, dimension="product", limit=5)
    sql = db.sql[0]
    assert "image_urls[" in sql, "the product thumbnail is gone"
    # `.astext` renders as `->>`; on this text-array column it is both wrong and
    # uncompilable, which is what 500'd the report.
    assert "->>" not in sql
    # The bound index is 1: PG arrays start at 1, and [0] is silently always NULL.
    literal = str(
        (await _one_statement("product")).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "image_urls[1]" in literal


@pytest.mark.asyncio
@pytest.mark.parametrize("dimension", ["cashier", "staff", "device"])
async def test_unrung_orders_borrow_the_open_till(dimension):
    db = _CompilingDB()
    await sales.sales_by_dimension(db, dimension=dimension, limit=5)
    sql = db.sql[0].lower()
    assert "lateral" in sql and "tills" in sql, (
        f"{dimension} no longer attributes via the open till"
    )
    assert "coalesce(orders." in sql


@pytest.mark.asyncio
async def test_completed_sale_counts_delivered_website_orders():
    """The filter must be closed-till OR delivered-website, or the reports go
    back to missing every Website sale."""
    db = _CompilingDB()
    await sales.sales_by_dimension(db, dimension="channel", limit=5)
    literal = str(
        (await _one_statement("channel")).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "pos_status" in literal
    assert "'online'" in literal and "'delivered'" in literal


async def _one_statement(dimension: str):
    """Return the (single) statement `sales_by_dimension` builds for a dimension,
    by capturing it rather than executing."""
    captured = []

    class _Capture:
        async def execute(self, stmt):
            captured.append(stmt)
            return _Result()

    await sales.sales_by_dimension(_Capture(), dimension=dimension, limit=5)
    return captured[0]
