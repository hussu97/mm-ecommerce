"""Shared visibility rules for products exposed to shoppers."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

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


def website_product_visibility_clause(
    branch_id: uuid.UUID | None = None,
    *,
    branch_ids: Sequence[uuid.UUID] | None = None,
):
    """
    The complete database predicate for a product a shopper can buy.

    Three answers, widest to narrowest:

    * **`branch_ids`** — the branches that can serve this shopper's pin (a
      polygon's priority list). The catalogue shows the *union* of them: a
      product is hidden only when every one of those branches is out of it,
      because the basket will give the order to whichever of them can make the
      whole thing. This is the truest answer once a pin is known.

    * **`branch_id`** — a single kitchen. The catalogue answers for that branch
      alone, the pre-multi-branch behaviour, kept for callers not yet handing us
      the set.

    * **neither** — the website-delivery union: a product every website branch
      has marked out is not buyable anywhere and so is not listed. The honest
      answer for a reader who has told us nothing — a crawler, or a first visit
      before the browser has resolved a location — and deliberately the widest,
      because a page cached for somebody with no address must not carry one
      branch's stockouts.

    The per-branch answer that actually survives the checkout is enforced later,
    at the cart and again at placement, where the basket resolves the same
    branches from the same pin and refuses what none of them can make.
    """
    if branch_ids:
        availability = ~availability_service.out_at_every_branch_in_set_subquery(
            branch_ids
        )
    elif branch_id is not None:
        availability = ~availability_service.unsellable_at_branch_subquery(branch_id)
    else:
        availability = ~availability_service.out_at_every_branch_subquery()
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
