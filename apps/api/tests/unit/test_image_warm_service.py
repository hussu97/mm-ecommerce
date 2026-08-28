"""
Warming used to be a script somebody had to remember to run.

`/_next/image` builds each derivative on the first request for it, and that
first request measured 0.5s to 9.8s against production. Nothing surfaces that
in the console — whoever uploads an image warms it themselves by looking at it,
so the wait always lands on a customer opening a page nobody has opened yet.
The warm now fires from the three places an image URL enters the catalogue:
an admin upload, a Foodics bulk import, and a CMS content change.

What matters here is which URLs get warmed. Warming a path the storefront never
sends through the optimiser spends transforms on a derivative no page will ever
request, and missing one puts the wait back on a customer.
"""

from __future__ import annotations

import pytest

from app.services.image_warm_service import (
    MAX_IMAGES_PER_RUN,
    QUALITY,
    WARM_WIDTHS,
    _variants,
    collect_image_urls,
    warm,
    warmable,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://storage.googleapis.com/mm-product-images/menu/abc.jpg",
        "https://storage.googleapis.com/mm-product-images/products/uuid.png",
        "https://media.meltingmomentscakes.com/banners/promo.jpg",
        "/images/tiles/tile-cakes.jpg",
    ],
)
def test_optimised_images_are_warmed(url):
    assert warmable(url)


@pytest.mark.parametrize(
    "url",
    [
        # The hero and promo bands render a hand-written <picture> against
        # pre-built AVIF/WebP siblings and never touch the optimiser, so these
        # would be derivatives nobody requests.
        "/images/banners/hero-mix-box.jpg",
        "/images/banners/hero-mix-box-mobile.jpg",
        "/IMAGES/BANNERS/shouting.jpg",
        None,
        "",
        "   ",
    ],
)
def test_art_directed_and_empty_are_not_warmed(url):
    assert not warmable(url)


def test_every_reachable_width_is_requested_at_the_configured_quality():
    variants = _variants("https://example.com/a.jpg")

    assert len(variants) == len(WARM_WIDTHS)
    for width, href in zip(WARM_WIDTHS, variants):
        assert f"w={width}" in href
        assert f"q={QUALITY}" in href
    # The source URL is a query parameter, so it has to survive encoding intact.
    assert "url=https%3A%2F%2Fexample.com%2Fa.jpg" in variants[0]


def test_image_urls_are_found_anywhere_in_a_cms_blob():
    # The CMS content shape differs per page and per section, so the walk cannot
    # depend on knowing which keys hold images.
    content = {
        "hero": {
            "slides": [
                {"image": "https://media.example.com/a.jpg", "headline": "Hello"},
                {"image_mobile": "https://media.example.com/b.png"},
            ]
        },
        "promos": {"items": [{"image": "/images/tiles/tile-cakes.jpg"}]},
        "cta_href": "/all-products",
        "body": "A sentence that mentions nothing.jpg-like but is not a URL",
    }

    found = collect_image_urls(content)

    assert "https://media.example.com/a.jpg" in found
    assert "https://media.example.com/b.png" in found
    assert "/images/tiles/tile-cakes.jpg" in found
    assert "/all-products" not in found


def test_prose_is_not_mistaken_for_a_url():
    assert collect_image_urls({"body": "read our guide to cake.png files"}) == []


@pytest.mark.asyncio
async def test_nothing_to_warm_makes_no_requests():
    # An import with no image column, or a CMS edit that only changed copy,
    # must not open a connection to the storefront at all.
    assert await warm([]) == 0
    assert await warm(["/images/banners/hero-mix-box.jpg"]) == 0


@pytest.mark.asyncio
async def test_a_repeated_bulk_import_cannot_spawn_unbounded_work(caplog):
    # Re-uploading the same CSV five times should not queue five full catalogue
    # warms on top of each other. What gets dropped is logged, not swallowed.
    urls = [f"https://example.invalid/{i}.jpg" for i in range(MAX_IMAGES_PER_RUN + 25)]

    with caplog.at_level("WARNING"):
        await warm(urls)

    assert any("image warm capped" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_an_unreachable_storefront_never_raises():
    # This runs as a background task attached to an admin action that has
    # already succeeded. A storefront mid-redeploy is not a reason to blow up.
    assert await warm(["https://example.invalid/a.jpg"]) == 0
