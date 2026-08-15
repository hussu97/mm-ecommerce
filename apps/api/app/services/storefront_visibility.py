"""Shared visibility rules for products exposed to shoppers."""

from __future__ import annotations

from sqlalchemy import or_

from app.models.category import Category
from app.models.product import WEB_CHANNEL, Product, sells_on
from app.services import availability_service


def active_website_category_clause():
    """Allow uncategorised products, otherwise require a live category."""
    return or_(
        Product.category_id.is_(None),
        Product.category.has(Category.is_active.is_(True)),
    )


def website_product_visibility_clause():
    """
    The complete database predicate for a product a shopper can buy.

    Includes the catalogue-wide half of branch availability: a product every
    active branch has marked out is not buyable anywhere and so is not listed.
    The *per-branch* half cannot live here — a shopper browsing has given us no
    address and there is no branch to ask about yet — and is applied at the cart
    and again at placement, where the delivery zone has named one.

    Deliberately not "out at the nearest branch": hiding a cake from somebody
    whose own shop has it on the shelf is a worse failure than offering one that
    turns out to need a different branch, and the second is what the checkout
    resolution screen exists to catch.
    """
    return (
        Product.is_active.is_(True),
        sells_on(WEB_CHANNEL),
        active_website_category_clause(),
        ~availability_service.out_at_every_branch_subquery(),
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
