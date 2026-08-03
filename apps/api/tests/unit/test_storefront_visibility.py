"""Regression tests for the shopper-facing visibility boundary."""

from types import SimpleNamespace

from app.services.storefront_visibility import is_website_product_visible


def product(
    *, active=True, channels=None, category_id="category", category_active=True
):
    return SimpleNamespace(
        is_active=active,
        sales_channels=channels if channels is not None else ["web"],
        category_id=category_id,
        category=(
            SimpleNamespace(is_active=category_active)
            if category_id is not None
            else None
        ),
    )


def test_a_product_in_a_hidden_category_is_not_storefront_visible():
    assert not is_website_product_visible(product(category_active=False))


def test_the_visibility_boundary_requires_an_active_web_product():
    assert is_website_product_visible(product())
    assert not is_website_product_visible(product(active=False))
    assert not is_website_product_visible(product(channels=["pos"]))
    assert is_website_product_visible(product(category_id=None))
