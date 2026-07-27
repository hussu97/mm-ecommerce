"""
Categories follow the catalogue, not the product table.

The counter sells coffee and bottled water imported from Foodics; the website
sells cakes. A category whose every product is POS-only has nothing to show a
customer, so listing it gives them a tab that opens onto an empty page. These
tests pin the queries that keep those categories off the storefront while
leaving admin's view of the full catalogue alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.core.exceptions import NotFoundError
from app.models.category import Category
from app.services import category_service


def _category(**overrides) -> Category:
    now = datetime.now(UTC)
    fields = {
        "id": uuid4(),
        "name": "Coffee",
        "slug": "coffee",
        "reference": None,
        "description": None,
        "image_url": None,
        "translations": {},
        "display_order": 0,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    fields.update(overrides)
    return Category(**fields)


def _capturing_db(rows):
    """A session that records the statement it was handed and returns `rows`."""
    captured = []
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    result.first = MagicMock(return_value=rows[0] if rows else None)

    async def execute(stmt, *args, **kwargs):
        captured.append(stmt)
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    db.captured = captured
    return db


def _sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


async def test_storefront_listing_counts_only_products_the_website_sells():
    db = _capturing_db([])
    await category_service.get_all(db)

    sql = _sql(db.captured[0])
    assert "is_web_visible" in sql
    assert "is_active" in sql


async def test_storefront_listing_drops_categories_with_nothing_to_sell():
    """
    The bug this file exists for: a category of POS-only items showed up as a
    tab on the website. A count of zero must remove the row, not render it.
    """
    db = _capturing_db([])
    await category_service.get_all(db)

    assert "HAVING" in _sql(db.captured[0]).upper()


async def test_admin_still_sees_every_category_and_the_whole_catalogue():
    """
    Admin manages what the storefront hides. Filtering its list would make
    POS-only categories uneditable — and invisible right when someone needs to
    find out why they vanished from the site.
    """
    db = _capturing_db([])
    await category_service.get_all(db, include_inactive=True)

    sql = _sql(db.captured[0])
    assert "is_web_visible" not in sql
    assert "HAVING" not in sql.upper()


async def test_a_hidden_category_is_not_reachable_by_guessing_its_url():
    """Filtering the list but not the detail page still serves the empty page."""
    db = _capturing_db([(_category(), 0)])

    with pytest.raises(NotFoundError):
        await category_service.get_by_slug(db, "coffee")


async def test_the_detail_page_still_serves_a_category_that_has_web_products():
    db = _capturing_db([(_category(slug="cakes", name="Cakes"), 4)])

    response = await category_service.get_by_slug(db, "cakes")

    assert response.slug == "cakes"
    assert response.product_count == 4


async def test_admin_can_still_fetch_a_category_with_no_web_products():
    db = _capturing_db([(_category(), 0)])

    response = await category_service.get_by_slug(db, "coffee", include_inactive=True)

    assert response.slug == "coffee"
