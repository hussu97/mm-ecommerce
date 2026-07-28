"""
The console manages the whole catalogue, not just the website's share of it.

`channel` defaults to "web" on the API so a forgotten parameter can never put
counter items on a cake website. That default is right there and wrong in the
console, which has to reach all 131 products — and the admin client sent no
channel at all, so it saw the 39 web-visible ones and had no route to the 92
sold only at the till.

The tell was a "POS only" badge on the products page that could never render:
the products it describes were filtered out before they reached it.
"""

from __future__ import annotations

import inspect
import pathlib

from app.api.v1 import products as products_api
from app.services import product_service

ADMIN = pathlib.Path(__file__).parents[3] / "admin"


def test_the_api_still_defaults_to_the_website():
    """The safe default must stay put — the fix belongs in the caller."""
    source = inspect.getsource(products_api.list_products)
    assert '"web",' in source


def test_the_admin_client_asks_for_every_channel():
    client = (ADMIN / "lib/api.ts").read_text()
    assert "params.channel ?? 'all'" in client
    assert "'/products?channel=all'" in client, "the no-argument path too"


def test_a_staff_viewer_can_open_any_product():
    """
    Listing a counter item and then 404ing when you click it is worse than not
    listing it, and inactive products were unreachable for editing.
    """
    source = inspect.getsource(products_api.get_product)
    assert "viewer.is_staff or viewer.is_admin" in source
    assert "get_by_slug_admin" in source


def test_a_shopper_still_only_sees_the_live_website():
    source = inspect.getsource(product_service.get_by_slug)
    assert "sells_on(WEB_CHANNEL)" in source
    assert "is_active" in source


def test_the_admin_variant_does_not_filter_by_channel():
    source = inspect.getsource(product_service.get_by_slug_admin)
    assert "sells_on" not in source


def test_the_menu_group_editor_pages_the_catalogue():
    """
    It asked for per_page=1000; the API caps at 100 and 422s above it, so the
    editor loaded nothing and sat on its spinner.
    """
    page = (ADMIN / "app/(dashboard)/menu-groups/page.tsx").read_text()
    assert "CATALOGUE_PAGE_SIZE = 100" in page
    assert "per_page: 1000" not in page
    assert "setLoadError" in page, "a failed load must say so, not hang"
