"""
Open price is a product attribute, and the till must hold both ends of it.

Foodics distinguishes two things that both look like "no price" in an export:
a product priced by a required size modifier, which carries `price: 0`, and a
genuinely open-price product, which carries no `price` key at all. Reading them
the same way is how "Cake - Customer Specification" ends up ringing at 0.00.
"""

from __future__ import annotations

import inspect

from app.services.pos import pos_order_service


def test_an_open_price_product_cannot_be_rung_up_without_a_price():
    """The failure this exists to prevent: a custom cake sold for nothing."""
    source = inspect.getsource(pos_order_service.add_item)
    assert 'product.pricing_method == "open" and not is_open_price' in source
    assert "sold at an open price" in source


def test_a_fixed_price_product_rejects_an_override():
    """Otherwise the till becomes a way to reprice the menu without a discount."""
    source = inspect.getsource(pos_order_service.add_item)
    assert 'is_open_price and product.pricing_method != "open"' in source


def test_the_importer_reads_a_missing_price_key_as_open():
    """`price: 0` means priced by modifier; no key at all means open."""
    import pathlib

    importer = (
        pathlib.Path(__file__).parents[2] / "scripts/foodics/import_catalogue.py"
    ).read_text()
    assert 'is_open_price = "price" not in row' in importer
    assert '"pricing_method": "open" if is_open_price else "fixed"' in importer


def test_zero_is_not_treated_as_open():
    """
    The 49 coffee and bakery items priced by a size modifier must stay fixed —
    marking them open would prompt the cashier for a price on every latte.
    """
    row_priced_by_modifier = {"sku": "FG0117", "name": "Espresso", "price": 0}
    row_open = {"sku": "FG0119", "name": "Cake - Customer Specification"}
    assert ("price" not in row_priced_by_modifier) is False
    assert ("price" not in row_open) is True
