"""
A category may not take a URL the storefront already answers on.

Until migration 120 every category slug was `cat-`-prefixed, so this could not
happen: `/en/cat-about` and `/en/about` were never going to collide. Readable
slugs give that guarantee up — category pages live at `/{locale}/{slug}`, the
same level as `/en/checkout` — and Next resolves the static segment first. So a
category called "Search" would exist in the console, appear in the navigation,
and lead to the search page. Nothing would error; the category would simply have
no page, and the reason would be invisible.

The guard runs before the session is touched, which is why these can use a mock:
what is being asserted is that the request is refused, not what the database
would otherwise have done with it. The accepting path needs a real session to
build a response from, and is covered by the integration suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import ConflictError
from app.schemas.category import CategoryCreate, CategoryUpdate
from app.services.catalog import category_service
from app.services.catalog.category_service import RESERVED_SLUGS


@pytest.mark.parametrize(
    "slug", ["about", "checkout", "blog", "all-products", "search"]
)
async def test_create_refuses_a_storefront_route(mock_db, slug):
    with pytest.raises(ConflictError) as err:
        await category_service.create(
            mock_db, CategoryCreate(name="Something", slug=slug)
        )
    # The message has to name the slug: an operator who typed it needs to know
    # which of the fields they filled in is the problem.
    assert slug in str(err.value)


async def test_update_refuses_renaming_onto_a_storefront_route(mock_db):
    """
    The half that would otherwise be missed.

    Creation is the obvious place to guard; the rename is the one that actually
    happens. A category that has existed for a year being tidied up in the
    console is exactly how a live listing walks into `/faq`.
    """
    from app.models.category import Category

    mock_db.execute.return_value.scalar_one_or_none.return_value = Category(
        name="Frequently Asked", slug="questions"
    )

    with pytest.raises(ConflictError) as err:
        await category_service.update(mock_db, "questions", CategoryUpdate(slug="faq"))
    assert "faq" in str(err.value)


async def test_a_slug_that_merely_contains_a_reserved_word_is_allowed(mock_db):
    """
    `about` is a page; `about-our-cakes` is not.

    Membership, not a substring match — a prefix test here would refuse
    `cakes-for-search-parties` and `blogger-boxes` for no reason, and the
    operator would have no way to tell why.
    """
    for slug in ["about-our-cakes", "blogger-boxes", "searchlight-cakes"]:
        assert slug not in RESERVED_SLUGS


def test_the_reserved_list_matches_the_storefront_routes():
    """
    The list is a copy of a directory listing, and copies drift.

    A new top-level page added under `app/[locale]/` without a line in
    `RESERVED_SLUGS` re-opens the exact hole this closes: the page exists, a
    category is allowed to claim its URL, and the category silently has no page.
    So the directory is read and compared rather than trusted.

    Skipped where the storefront is not on disk — the API image ships without
    it. In the monorepo, which is where this drift happens and where CI runs, it
    runs.
    """
    routes = Path(__file__).resolve().parents[3] / "web" / "app" / "[locale]"
    if not routes.is_dir():
        pytest.skip("storefront routes not on disk")

    on_disk = {
        d.name for d in routes.iterdir() if d.is_dir() and not d.name.startswith("[")
    }
    assert on_disk == set(RESERVED_SLUGS), (
        "RESERVED_SLUGS has drifted from app/[locale]/. "
        f"Missing: {sorted(on_disk - set(RESERVED_SLUGS))}. "
        f"Stale: {sorted(set(RESERVED_SLUGS) - on_disk)}."
    )
