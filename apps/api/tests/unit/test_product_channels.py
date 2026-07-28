"""
The storefront and the POS sell different catalogues.

Melting Moments' website sells cakes. The counter also sells coffee, juices
and bottled water, imported from Foodics. Those must never appear on the
website, and the mechanism that keeps them apart has to fail safe.
"""

from __future__ import annotations

import inspect

from app.services import product_service


def test_listing_defaults_to_the_website_catalogue():
    """
    The default matters more than the feature.

    The storefront and the terminal share one query. If `channel` defaulted to
    "all", every forgotten parameter would put bottled water on a cake
    website; defaulting to "web" means the worst a mistake can do is show too
    few products to a cashier, which is visible and harmless.
    """
    default = inspect.signature(product_service.get_all).parameters["channel"].default
    assert default == "web"


def test_the_storefront_query_filters_on_web_visibility():
    source = inspect.getsource(product_service.get_all)
    assert 'channel == "web"' in source
    assert "sells_on(WEB_CHANNEL)" in source


def test_single_product_and_featured_lookups_are_storefront_only():
    """
    A POS-only item must not be reachable by guessing its URL.

    Filtering the list but not the detail page would still leave
    /products/spanish-latte serving a product the website does not sell.
    """
    for fn in (product_service.get_by_slug, product_service.get_featured):
        assert "sells_on(WEB_CHANNEL)" in inspect.getsource(fn), fn.__name__
