"""
The storefront purge: what it deletes, what it tells the web, and when it
keeps quiet. The session-event half (which writes trigger it) needs a real
database and lives in `tests/integration/test_storefront_cache_db.py`.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.services import storefront_cache
from app.services.catalog import catalogue_cache

WEB_ROOT = Path(__file__).resolve().parents[3] / "web"


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(storefront_cache, "REDELETE_DELAY", 0)
    monkeypatch.setattr(storefront_cache, "_RETRY_DELAYS", (0, 0))
    storefront_cache._pending.clear()


@pytest.fixture
def deleted(monkeypatch):
    seen: list[str] = []

    async def _delete(pattern):
        seen.append(pattern)

    monkeypatch.setattr(storefront_cache, "cache_delete_pattern", _delete)
    return seen


@pytest.fixture
def web(monkeypatch):
    """The storefront, as an httpx mock transport; set `.status` to answer."""

    class Web:
        status = 200
        calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        Web.calls.append(request)
        return httpx.Response(Web.status, json={})

    real = httpx.AsyncClient

    def client(*args, **kwargs):
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    Web.calls = []
    monkeypatch.setattr(storefront_cache.httpx, "AsyncClient", client)
    return Web


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(
        settings, "STOREFRONT_REVALIDATE_URL", "https://shop.test/api/revalidate"
    )
    monkeypatch.setattr(settings, "STOREFRONT_REVALIDATE_SECRET", "s3cret")


async def _settle():
    # The purge is fire-and-forget: let the spawned tasks run to completion.
    for _ in range(20):
        await asyncio.sleep(0)
    await storefront_cache.flush_pending()


# ─── Tags ─────────────────────────────────────────────────────────────────────


def test_web_tags_match_what_the_storefront_fetches_with():
    source = (WEB_ROOT / "lib" / "cache-policy.ts").read_text()
    block = source[source.index("export const CACHE_TAGS") :]
    block = block[: block.index("} as const")]
    web_tags = set(re.findall(r"^\s+\w+: '([a-z0-9]+)',", block, re.M))
    assert web_tags == set(storefront_cache.WEB_TAGS)


def test_catalogue_purge_covers_every_key_retire_deletes():
    assert set(catalogue_cache._PATTERNS) <= set(
        storefront_cache.REDIS_PATTERNS[storefront_cache.CATALOGUE]
    )


def test_every_tagged_table_has_keys_or_a_web_tag():
    for table, tags in storefront_cache.TABLE_TAGS.items():
        for tag in tags:
            assert storefront_cache.REDIS_PATTERNS.get(tag) or (
                tag in storefront_cache.WEB_TAGS
            ), table


# ─── The purge ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unconfigured_purges_redis_and_never_calls_the_web(fast, deleted, web):
    storefront_cache.purge({"catalogue"})
    await _settle()
    assert "products:page:*" in deleted
    assert "categories:channel:*" in deleted
    # Twice: once straight after the commit, once after the re-delete delay.
    assert deleted.count("products:page:*") == 2
    assert web.calls == []


@pytest.mark.asyncio
async def test_configured_posts_web_tags_with_the_secret(
    fast, deleted, web, configured
):
    storefront_cache.purge({"catalogue", "redirects"})
    storefront_cache.purge({"promo"})
    await _settle()
    assert len(web.calls) == 1, "commits inside one delay coalesce into one call"
    request = web.calls[0]
    assert str(request.url) == "https://shop.test/api/revalidate"
    assert request.headers["authorization"] == "Bearer s3cret"
    # `redirects` has no web cache to purge; its Redis key still went.
    assert json.loads(request.content) == {"tags": ["catalogue", "promo"]}
    assert "redirects:*" in deleted


@pytest.mark.asyncio
async def test_a_refusal_is_not_retried(fast, deleted, web, configured):
    web.status = 401
    storefront_cache.purge({"cms"})
    await _settle()
    assert len(web.calls) == 1


@pytest.mark.asyncio
async def test_a_server_error_is_retried_then_given_up(fast, deleted, web, configured):
    web.status = 503
    storefront_cache.purge({"cms"})
    await _settle()
    assert len(web.calls) == 3


@pytest.mark.asyncio
async def test_half_configured_is_unconfigured(fast, deleted, web, monkeypatch):
    monkeypatch.setattr(settings, "STOREFRONT_REVALIDATE_URL", "https://shop.test/x")
    monkeypatch.setattr(settings, "STOREFRONT_REVALIDATE_SECRET", "")
    assert not storefront_cache.enabled()
    storefront_cache.purge({"cms"})
    await _settle()
    assert web.calls == []


def test_without_an_event_loop_it_does_nothing(deleted):
    storefront_cache._pending.clear()
    storefront_cache.purge({"catalogue"})
    assert deleted == []
    assert not storefront_cache._pending


# ─── Seconds to the next boundary ─────────────────────────────────────────────


def test_seconds_until_caps_and_rounds_up():
    now = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    assert storefront_cache._seconds_until(None, now, 300) == 300
    assert storefront_cache._seconds_until(now + timedelta(hours=1), now, 300) == 300
    # Gone by the time the moment has passed, never just before it.
    assert storefront_cache._seconds_until(now + timedelta(seconds=9.2), now, 300) == 11
    assert storefront_cache._seconds_until(now - timedelta(seconds=5), now, 300) == 1
