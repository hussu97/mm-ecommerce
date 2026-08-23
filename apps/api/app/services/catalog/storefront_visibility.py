"""Shared visibility rules for products exposed to shoppers."""

from __future__ import annotations

import uuid

from sqlalchemy import or_

from app.models.category import Category
from app.models.product import WEB_CHANNEL, Product, sells_on
from app.services.catalog import availability_service


def active_website_category_clause():
    """Allow uncategorised products, otherwise require a live category."""
    return or_(
        Product.category_id.is_(None),
        Product.category.has(Category.is_active.is_(True)),
    )


def website_product_visibility_clause(branch_id: uuid.UUID | None = None):
    """
    The complete database predicate for a product a shopper can buy.

    `branch_id` is the kitchen that would make this shopper's order, resolved
    from the pin they gave us. Given one, the catalogue answers for *that*
    branch and nothing else — which is the only answer that survives the
    checkout, because the checkout resolves the same branch from the same pin
    and refuses what it cannot make.

    Without one it falls back to the catalogue-wide half: a product every active
    branch has marked out is not buyable anywhere and so is not listed. That is
    the honest answer for a reader who has told us nothing — a crawler, or a
    first visit before the browser has resolved a location — and it is
    deliberately the *widest* answer, because a page cached for somebody with no
    address must not carry one branch's stockouts.

    It used to be the only answer, on the reasoning that hiding a cake from
    somebody whose own shop has it on the shelf is worse than offering one that
    needs a different branch. That reasoning holds exactly as far as "we do not
    know where they are". Once the pin has named a kitchen, showing that kitchen
    cannot make it is not caution, it is a promise nobody can keep.
    """
    availability = (
        ~availability_service.unsellable_at_branch_subquery(branch_id)
        if branch_id is not None
        else ~availability_service.out_at_every_branch_subquery()
    )
    return (
        Product.is_active.is_(True),
        sells_on(WEB_CHANNEL),
        active_website_category_clause(),
        availability,
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
