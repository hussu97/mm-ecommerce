"""
The three storefront reads cached in Redis for the on-demand purge:
`/products/{slug}`, `/promo-codes/featured` and `/i18n/languages`.

What matters: a shopper's answer is cached under who it is for, a staff answer
never is, a 404 never is, "no coupon running" is cached as an answer rather than
read as a miss, and the TTL is the one the boundary helper hands back.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.api.v1 import i18n as i18n_router
from app.api.v1 import products as products_router
from app.api.v1 import promo_codes as promo_router
from app.core.exceptions import NotFoundError
from app.schemas.i18n import LanguageResponse
from app.services import i18n_service, promo_code_service, storefront_cache
from app.services.catalog import product_service


class FakeRedis:
    def __init__(self):
        self.store: dict[str, object] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ttl=300):
        self.store[key] = value
        self.ttls[key] = ttl


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    for module in (products_router, promo_router, i18n_service):
        monkeypatch.setattr(module, "cache_get", fake.get)
        monkeypatch.setattr(module, "cache_set", fake.set)
    return fake


@pytest.fixture
def boundaries(monkeypatch):
    async def catalogue(db, cap):
        return 42

    async def promo(db, cap):
        return 17

    monkeypatch.setattr(
        storefront_cache, "seconds_to_next_catalogue_boundary", catalogue
    )
    monkeypatch.setattr(storefront_cache, "seconds_to_next_promo_boundary", promo)


class Page:
    def __init__(self, slug):
        self.slug = slug

    def model_dump(self, mode="json"):
        return {"slug": self.slug}


@pytest.mark.asyncio
async def test_product_page_is_cached_per_branch_set(redis, boundaries, monkeypatch):
    calls: list[tuple] = []

    async def get_by_slug(db, slug, *, branch_id=None, branch_ids=None):
        calls.append((slug, branch_id, tuple(branch_ids or ())))
        return Page(slug)

    monkeypatch.setattr(product_service, "get_by_slug", get_by_slug)
    b1, b2 = uuid.uuid4(), uuid.uuid4()

    first = await products_router.get_product("brownie", None, [b2, b1], None, None)
    again = await products_router.get_product("brownie", None, [b1, b2], None, None)
    estate = await products_router.get_product("brownie", None, None, None, None)

    assert isinstance(first, Page)
    assert again == {"slug": "brownie"}  # served from Redis: same set, any order
    assert isinstance(estate, Page)  # a different shopper scope is its own entry
    assert len(calls) == 2
    key = f"products:page:brownie:set:{','.join(sorted(map(str, (b1, b2))))}"
    assert redis.ttls[key] == 42


@pytest.mark.asyncio
async def test_staff_and_misses_are_never_cached(redis, boundaries, monkeypatch):
    async def admin(db, slug):
        return Page(slug)

    async def missing(db, slug, *, branch_id=None, branch_ids=None):
        raise NotFoundError("gone")

    monkeypatch.setattr(product_service, "get_by_slug_admin", admin)
    monkeypatch.setattr(product_service, "get_by_slug", missing)
    staff = SimpleNamespace(is_staff=True, is_admin=False)

    await products_router.get_product("brownie", None, None, None, staff)
    with pytest.raises(NotFoundError):
        await products_router.get_product("nope", None, None, None, None)
    assert redis.store == {}


@pytest.mark.asyncio
async def test_no_coupon_running_is_a_cached_answer(redis, boundaries, monkeypatch):
    calls = []

    async def advertisable(db):
        calls.append(1)
        return None

    monkeypatch.setattr(promo_code_service, "advertisable", advertisable)
    request = SimpleNamespace()
    unwrapped = promo_router.featured_promo_code.__wrapped__

    assert await unwrapped(request, None) is None
    assert await unwrapped(request, None) is None
    assert calls == [1]
    assert redis.store["promo:featured"] == {"promo": None}
    assert redis.ttls["promo:featured"] == 17


@pytest.mark.asyncio
async def test_languages_round_trip_through_redis(redis, monkeypatch):
    lang = LanguageResponse.model_validate(
        {
            "code": "en",
            "name": "English",
            "native_name": "English",
            "direction": "ltr",
            "is_default": True,
            "is_active": True,
            "display_order": 0,
        }
    )
    calls = []

    async def active(db):
        calls.append(1)
        return [lang]

    monkeypatch.setattr(i18n_service, "get_active_languages", active)
    response = SimpleNamespace(headers={})
    first = await i18n_router.list_languages(response, None)
    second = await i18n_router.list_languages(response, None)
    assert first == second == [lang]
    assert calls == [1]
