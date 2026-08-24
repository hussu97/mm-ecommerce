"""
The till-close channel breakdown, compiled to SQL without a database.

The report groups a shift's takings by channel — one row per marketplace plus
Website and Counter — over the till's open window. Like the sales-by-dimension
queries, the bug that would actually ship is a statement that *builds* wrong, so
this renders it to PostgreSQL and checks the load-bearing pieces are in the SQL:
the channel `CASE`, the completed-sale filter (so website revenue counts), the
refund column that nets the total, and the open-window bounds.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects import postgresql

from app.models.till import Till
from app.services.pos import till_service


class _Result:
    def all(self):
        return []


class _CompilingDB:
    """Renders each statement to SQL instead of running it."""

    def __init__(self):
        self.sql: list[str] = []

    async def execute(self, stmt):
        # literal_binds inlines the bound values, so the completed-sale filter's
        # 'closed' / 'online' / 'delivered' are visible in the rendered SQL
        # rather than hidden behind %(param)s placeholders.
        self.sql.append(
            str(
                stmt.compile(
                    dialect=postgresql.dialect(),
                    compile_kwargs={"literal_binds": True},
                )
            )
        )
        return _Result()


def _till() -> Till:
    till = Till()
    till.branch_id = uuid.uuid4()
    till.opened_at = datetime(2026, 8, 23, 5, 25, tzinfo=timezone.utc)
    till.closed_at = None
    return till


@pytest.mark.asyncio
async def test_channel_breakdown_compiles_to_postgres():
    """The statement must build for an open till (closed_at is None → 'now')."""
    result = await till_service._channel_breakdown(_CompilingDB(), _till())
    # No rows returned by the fake, so every total collapses to zero cleanly —
    # the point of the test is that it built and aggregated without raising.
    assert result["channels"] == []
    assert result["total_orders"] == 0
    assert str(result["total_revenue"]) == "0.00"
    assert str(result["net_payments"]) == "0.00"


@pytest.mark.asyncio
async def test_channel_breakdown_groups_and_filters_like_the_sales_report():
    db = _CompilingDB()
    await till_service._channel_breakdown(db, _till())
    sql = db.sql[0]

    # Grouped on the source / aggregator_channel split, so each marketplace is
    # its own row rather than one lumped "aggregator".
    assert "aggregator_channel" in sql
    assert "GROUP BY" in sql
    # Website revenue is counted — the completed-sale filter is closed OR
    # (online AND delivered), not closed alone.
    assert "delivered" in sql
    # The net line is revenue minus refunds, so the refund column has to be here.
    assert "refunded_amount" in sql
    # Bounded by the till's open window, not by till_id (aggregator/website
    # orders carry no till).
    assert "till_id" not in sql
