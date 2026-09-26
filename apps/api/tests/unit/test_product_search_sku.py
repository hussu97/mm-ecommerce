"""
The admin console finds products by SKU as well as name; the website does not.

Both share `get_all`. The console lists channel "all" (staff-only); the website
always lists channel "web", so the SKU match is gated on that — a shopper, or a
staff member browsing the site, typing "cm" does not get every CM-coded cake.
No database in this suite, so these read the SQL the query builds.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.services.catalog import product_service


async def _sql_for(**kwargs) -> str:
    statements: list[str] = []

    async def execute(statement):
        statements.append(str(statement.compile()))
        result = MagicMock()
        result.scalar.return_value = 0
        result.scalars.return_value.unique.return_value.all.return_value = []
        return result

    db = AsyncMock()
    db.execute = execute
    await product_service.get_all(db, **kwargs)
    return "\n".join(statements)


async def test_staff_search_matches_sku():
    sql = await _sql_for(
        search="FG0026", channel="all", staff=True, include_inactive=True
    )

    assert "lower(products.sku) LIKE" in sql
    assert "lower(products.name) LIKE" in sql


async def test_shopper_search_is_name_only():
    sql = await _sql_for(search="cm", channel="web")

    assert "lower(products.name) LIKE" in sql
    assert "lower(products.sku) LIKE" not in sql


async def test_staff_on_the_website_search_names_only():
    sql = await _sql_for(search="cm", channel="web", staff=True)

    assert "lower(products.sku) LIKE" not in sql
