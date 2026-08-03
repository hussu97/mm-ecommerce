"""Shared visibility rules for products exposed to shoppers."""

from __future__ import annotations

from sqlalchemy import or_

from app.models.category import Category
from app.models.product import WEB_CHANNEL, Product, sells_on


def active_website_category_clause():
    """Allow uncategorised products, otherwise require a live category."""
    return or_(
        Product.category_id.is_(None),
        Product.category.has(Category.is_active.is_(True)),
    )


def website_product_visibility_clause():
    """The complete database predicate for a product a shopper can buy."""
    return (
        Product.is_active.is_(True),
        sells_on(WEB_CHANNEL),
        active_website_category_clause(),
    )


def is_website_product_visible(product: Product | None) -> bool:
    """In-memory counterpart for products already loaded with a cart/order."""
    if (
        not product
        or not product.is_active
        or WEB_CHANNEL not in (product.sales_channels or [])
    ):
        return False
    return product.category_id is None or bool(
        product.category and product.category.is_active
    )
