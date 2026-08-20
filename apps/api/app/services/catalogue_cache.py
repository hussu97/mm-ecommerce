"""
Retiring what the storefront has already been told.

Three answers are cached in Redis for the shopper: the featured rail, the cart's
add-on tray, and the category list with its counts. All three are derived from
the catalogue *and* from what each branch has in stock, and all three are now
keyed by branch — one entry per kitchen plus the estate-wide one.

That second half is why this module exists. Editing a product has always retired
these; marking one out of stock never did, because the register's availability
endpoints are a different router that had no reason to know about a storefront
cache. It did not show, back when a stockout only mattered if every branch
had one. Now that the catalogue answers per branch, a cashier marking the last
pistachio kunafa out is a change the website is supposed to reflect, and it
would have sat behind a five-minute entry keyed to that exact branch.

One function, called from both routers, so the next surface that changes stock
has an obvious thing to call rather than three patterns to remember.
"""

from __future__ import annotations

from app.core.cache import cache_delete_pattern

__all__ = ["retire"]

#: Every key whose value depends on what is sellable. Patterns rather than
#: names because each is keyed by branch as well as by its own parameters, and
#: enumerating that product is how one variant gets left behind.
_PATTERNS = (
    "products:featured:*",
    "products:cart_addons:*",
    "categories:channel:*",
)


async def retire() -> None:
    """
    Drop every cached answer that depends on what a branch can sell.

    Deliberately blunt: three pattern deletes against a handful of keys, on a
    path taken when somebody presses a button rather than on a request. Working
    out which entries a single stockout could have changed would cost more to
    maintain than the deletes cost to run, and the failure mode of getting it
    subtly wrong is a website that quietly disagrees with the shop.
    """
    for pattern in _PATTERNS:
        await cache_delete_pattern(pattern)
