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
import re

from app.api.v1 import products as products_api
from app.services import product_service

ADMIN = pathlib.Path(__file__).parents[3] / "admin"


def test_the_api_still_defaults_to_the_website():
    """The safe default must stay put — the fix belongs in the caller."""
    source = inspect.getsource(products_api.list_products)
    assert '"web",' in source


def test_the_admin_client_asks_for_every_channel():
    """
    The console's product list must send `channel=all`, however it is spelled.

    Asserted on the defaulting expression rather than on a finished query
    string: the client builds its query through `buildQs` now, so the literal
    `'/products?channel=all'` this used to look for no longer appears anywhere
    even though the behaviour is unchanged. What matters — and what breaks the
    "POS only" badge when it goes — is that a caller who names no channel still
    gets every one of them.
    """
    client = (ADMIN / "lib/api.ts").read_text()
    assert "channel: params?.channel ?? 'all'" in client


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
    assert "website_product_visibility_clause" in source


def test_the_admin_variant_does_not_filter_by_channel():
    source = inspect.getsource(product_service.get_by_slug_admin)
    assert "sells_on" not in source


def test_the_menu_group_editor_pages_the_catalogue():
    """
    It asked for per_page=1000 when the API capped at 100, got a 422, and sat
    on its spinner having loaded nothing.

    The cap has since risen to 2000, so the page size is read off the API's own
    limit rather than pinned to a number here — a test asserting `= 100` would
    now be asserting the editor makes twenty requests where one would do. What
    still has to hold is that the page never asks for more than the API allows,
    and that a refusal is shown rather than swallowed.
    """
    page = (ADMIN / "app/(dashboard)/menu-groups/page.tsx").read_text()
    cap = inspect.getsource(products_api.list_products)
    assert "le=2000" in cap, "the API's cap moved; the editor's page size follows it"

    sizes = re.findall(r"CATALOGUE_PAGE_SIZE\s*=\s*(\d+)", page)
    assert sizes, "the editor must declare its page size in one place"
    assert int(sizes[0]) <= 2000, "asking above the cap is a 422 and a dead spinner"
    assert "setLoadError" in page or "loadError" in page, (
        "a failed load must say so, not hang"
    )
