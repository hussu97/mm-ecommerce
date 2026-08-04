"""
Why the storefront must never render `checkout.estimated_delivery` at a customer.

Translations are read on every server render, so they are cached. The cache is
in Redis rather than in the process because an admin edit has to reach every
worker, not the one that happened to serve the save.

The failure this exists to prevent is subtler than a stale string. Redis outlives
an API restart, and the API deploy runs alongside the Vercel build. A deploy that
adds a translation key writes it to Postgres during startup and — without an
explicit invalidation — carries on serving the copy taken *before* the key
existed. The storefront's fallback for a missing key is the key itself, so real
customers get `checkout.estimated_delivery` printed on the checkout page for as
long as the TTL lasts. That is exactly what shipped once, and this is the guard.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.services import i18n_service


async def test_a_cached_locale_is_served_without_touching_the_database():
    db = AsyncMock()
    with patch.object(
        i18n_service,
        "cache_get",
        new=AsyncMock(return_value={"checkout.total": "Total"}),
    ):
        result = await i18n_service.get_translations(db, "en")

    assert result == {"checkout.total": "Total"}
    db.execute.assert_not_awaited()


async def test_a_write_drops_the_cached_copy():
    """
    Otherwise the admin saves a string, sees the old one, and saves it again.
    """
    deleted: list[str] = []
    with patch.object(
        i18n_service,
        "cache_delete",
        new=AsyncMock(side_effect=lambda key: deleted.append(key)),
    ):
        await i18n_service.invalidate_translations("ar")

    assert deleted == ["i18n:translations:ar"]


async def test_invalidating_without_a_locale_clears_every_language():
    """
    The seed adds keys to all of them at once, and it does not know or care
    which locales are configured.
    """
    deleted: list[str] = []
    with patch.object(
        i18n_service,
        "cache_delete",
        new=AsyncMock(side_effect=lambda key: deleted.append(key)),
    ):
        await i18n_service.invalidate_translations()

    assert set(deleted) == {"i18n:translations:en", "i18n:translations:ar"}


async def test_the_seed_invalidates_after_committing():
    """
    The whole point. A deploy that adds a key must not leave the pre-deploy copy
    in Redis, because the storefront renders a missing key as its own name.
    """
    import inspect

    from scripts import seed_i18n

    source = inspect.getsource(seed_i18n.seed)
    assert "invalidate_translations" in source, (
        "the i18n seed no longer drops the cached copy — a deploy that adds a "
        "key will render it as a raw key name on the storefront until the TTL "
        "lapses"
    )
    assert source.index("commit") < source.index("invalidate_translations"), (
        "invalidation runs before the write is committed"
    )
