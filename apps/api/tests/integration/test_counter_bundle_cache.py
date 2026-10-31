"""
The counter bundle is built once per change, not once per poll — over HTTP
against the register app and a real Postgres.

What has to hold:

* a repeat poll (200 or 304) reuses the built body: no body table is read and
  the `pos_config_bundles` row is not rewritten;
* any committed ORM write to a body table (a row edit, a bulk `update()`)
  retires it, but only once the OUTERMOST transaction commits;
* a write the session events cannot see (raw SQL) is picked up within
  `TTL_CEILING`;
* a usage-limited promotion still drops out the poll after it is used up;
* the hash a terminal is handed always has a committed `pos_config_bundles`
  row, even when the request that first built it rolled back;
* `BUNDLE_INPUT_TABLES` names every table `build_body` reads.
"""

from __future__ import annotations

import os
import re
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.devices import hash_device_token
from app.core.deps import get_db
from app.models.branch import Branch
from app.models.device import Device
from app.models.marketing import Promotion
from app.models.pos_counter import PosConfigBundle
from app.models.product import Product
from app.pos_main import app as pos_app
from app.services.pos import (
    auto_promotion_service,
    counter_bundle_cache,
    counter_bundle_service,
)

from ._counter_world import NEW_BUILD, build_world

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

BUNDLE = "/api/v1/pos/counter/bundle"

#: Tables only the build reads. The envelope reads `branches` (and the
#: relationships `Branch` loads eagerly) and the 86 list on every poll.
_BODY_ONLY = {
    "products",
    "menu_groups",
    "menu_group_products",
    "modifiers",
    "modifier_options",
    "product_modifiers",
    "promotions",
    "tax_groups",
    "taxes",
    "tax_group_taxes",
    "payment_methods",
    "reasons",
    "kitchen_flows",
    "legal_entities",
}


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
def Session(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def statements(engine):
    """Every SQL statement the engine runs, from the moment it is cleared."""
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", _record)


@pytest.fixture
async def shop(Session):
    token = uuid.uuid4().hex
    async with Session() as db:
        world = await build_world(db)
        device = await db.get(Device, world.device_id)
        device.token_hash = hash_device_token(token)
        branch = await db.get(Branch, world.branch_id)
        branch.counter_local_first = "on"
        await db.commit()
    return world, token


@pytest.fixture
def client(Session):
    async def override():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    pos_app.dependency_overrides[get_db] = override
    yield AsyncClient(transport=ASGITransport(app=pos_app), base_url="http://pos")
    pos_app.dependency_overrides.clear()


@pytest.fixture
def builds(monkeypatch):
    """How many times the body was built from the database."""
    calls: list[uuid.UUID] = []
    real = counter_bundle_service.build_body

    async def spy(db, branch, **kwargs):
        calls.append(branch.id)
        return await real(db, branch, **kwargs)

    monkeypatch.setattr(counter_bundle_service, "build_body", spy)
    return calls


def _headers(token: str, **extra) -> dict:
    return {
        "X-Device-Token": token,
        "X-App-Platform": "ios",
        "X-App-Build": NEW_BUILD,
        **extra,
    }


def _tables(sql: list[str]) -> set[str]:
    return {
        m.group(2)
        for s in sql
        for m in re.finditer(r"\b(FROM|JOIN|UPDATE|INTO)\s+\"?(\w+)", s)
    }


def _price(resp, product_id) -> str:
    products = resp.json()["bundle"]["products"]
    return next(p for p in products if p["id"] == str(product_id))["base_price"]


async def test_a_repeat_poll_reuses_the_body(shop, client, builds, statements):
    world, token = shop
    statements.clear()
    first = await client.get(BUNDLE, headers=_headers(token))
    assert first.status_code == 200
    miss = len(statements)

    statements.clear()
    again = await client.get(BUNDLE, headers=_headers(token))
    assert again.status_code == 200
    assert again.json() == {
        **first.json(),
        "envelope": {
            **first.json()["envelope"],
            "server_time": again.json()["envelope"]["server_time"],
        },
    }
    hit = len(statements)
    assert not _tables(statements) & _BODY_ONLY, "a reuse reads no body table"
    assert "pos_config_bundles" not in _tables(statements), "nor rewrites the row"

    statements.clear()
    not_modified = await client.get(
        BUNDLE, headers=_headers(token, **{"If-None-Match": first.headers["ETag"]})
    )
    assert not_modified.status_code == 304
    assert not_modified.headers["ETag"] == first.headers["ETag"]
    assert not_modified.headers["X-Counter-Mode"] == "on"
    short = len(statements)
    assert not any("display_number" in s for s in statements), (
        "a 304 skips the ticket-sequence read"
    )

    assert builds == [world.branch_id]
    print(f"\nqueries per bundle call: build {miss}, reuse {hit}, 304 {short}")
    assert hit < miss and short < hit


async def test_an_orm_edit_retires_the_body(shop, client, builds, Session):
    world, token = shop
    first = await client.get(BUNDLE, headers=_headers(token))
    async with Session() as db:
        (await db.get(Product, world.brownie_id)).base_price = Decimal("23.00")
        await db.commit()
    after = await client.get(
        BUNDLE, headers=_headers(token, **{"If-None-Match": first.headers["ETag"]})
    )
    assert after.status_code == 200
    assert _price(after, world.brownie_id) == "23.00"
    assert after.json()["hash"] != first.json()["hash"]
    assert len(builds) == 2


async def test_a_bulk_update_retires_the_body(shop, client, builds, Session):
    world, token = shop
    await client.get(BUNDLE, headers=_headers(token))
    async with Session() as db:
        await db.execute(
            update(Product)
            .where(Product.id == world.cookie_id)
            .values(base_price=Decimal("13.00"))
        )
        await db.commit()
    after = await client.get(BUNDLE, headers=_headers(token))
    assert _price(after, world.cookie_id) == "13.00"
    assert len(builds) == 2


async def test_a_savepoint_does_not_bump_before_the_outer_commit(shop, Session):
    world, _ = shop
    start = await counter_bundle_cache.generation()
    async with Session() as db:
        async with db.begin_nested():
            (await db.get(Product, world.brownie_id)).base_price = Decimal("24.00")
        assert await counter_bundle_cache.generation() == start, (
            "the write is not visible to anyone else yet"
        )
        await db.commit()
    assert (await counter_bundle_cache.generation())[0] == start[0] + 1


async def test_an_unrelated_write_keeps_the_body(shop, client, builds, Session):
    world, token = shop
    await client.get(BUNDLE, headers=_headers(token))
    async with Session() as db:
        (await db.get(Device, world.device_id)).name = "Renamed till"
        await db.commit()
    await client.get(BUNDLE, headers=_headers(token))
    assert len(builds) == 1


async def test_raw_sql_is_caught_by_the_ttl_ceiling(
    shop, client, builds, Session, monkeypatch
):
    world, token = shop
    await client.get(BUNDLE, headers=_headers(token))
    async with Session() as db:
        await db.execute(
            text("UPDATE products SET base_price = 30 WHERE id = :id"),
            {"id": world.brownie_id},
        )
        await db.commit()
    stale = await client.get(BUNDLE, headers=_headers(token))
    assert _price(stale, world.brownie_id) == "21.00", "unseen until the ceiling"

    real = counter_bundle_cache.time.monotonic
    monkeypatch.setattr(
        counter_bundle_cache.time,
        "monotonic",
        lambda: real() + counter_bundle_cache.TTL_CEILING,
    )
    fresh = await client.get(BUNDLE, headers=_headers(token))
    assert _price(fresh, world.brownie_id) == "30.00"
    assert len(builds) == 2


async def test_a_used_up_promotion_drops_out_on_the_next_poll(
    shop, client, builds, Session, monkeypatch
):
    world, token = shop
    async with Session() as db:
        promo = Promotion(
            name="First ten",
            type="basic",
            trigger="spend",
            trigger_value=Decimal("0"),
            reward="percentage_off_order",
            reward_value=Decimal("10"),
            sources=["cashier"],
            auto_branch_ids=[world.branch_id],
            auto_apply=True,
            priority=100,
            usage_limit=10,
        )
        db.add(promo)
        await db.commit()
        promo_id = promo.id

    used = {promo_id: 9}

    async def usage_counts(_db, ids=None):
        return {pid: n for pid, n in used.items() if ids is None or pid in ids}

    monkeypatch.setattr(auto_promotion_service, "usage_counts", usage_counts)

    def promos(resp):
        return [p["id"] for p in resp.json()["bundle"]["promotions"]]

    first = await client.get(BUNDLE, headers=_headers(token))
    assert promos(first) == [str(promo_id)]
    assert promos(await client.get(BUNDLE, headers=_headers(token))) == [str(promo_id)]
    assert len(builds) == 1, "usage below the limit: reused"

    used[promo_id] = 10  # a sale used it up — an order write, not an edit
    spent = await client.get(BUNDLE, headers=_headers(token))
    assert promos(spent) == []
    assert len(builds) == 2

    used[promo_id] = 9  # that sale was voided
    assert promos(await client.get(BUNDLE, headers=_headers(token))) == [str(promo_id)]
    assert len(builds) == 3


async def test_a_hash_is_persisted_even_if_its_first_request_rolled_back(
    shop, Session, builds
):
    world, _ = shop
    async with Session() as db:
        device = await db.get(Device, world.device_id)
        served = await counter_bundle_service.serve(
            db, device=device, build_number=NEW_BUILD
        )
        await db.rollback()
    async with Session() as db:
        assert await db.get(PosConfigBundle, served.hash) is None

    async with Session() as db:
        device = await db.get(Device, world.device_id)
        again = await counter_bundle_service.serve(
            db, device=device, build_number=NEW_BUILD
        )
        await db.commit()
    assert again.hash == served.hash and len(builds) == 1, "reused, not rebuilt"
    async with Session() as db:
        assert await db.get(PosConfigBundle, served.hash) is not None
    assert counter_bundle_cache._entries[world.branch_id].persisted


async def test_every_table_the_build_reads_is_an_input(shop, Session, statements):
    """A table `build_body` reads but `BUNDLE_INPUT_TABLES` omits is a write
    the cache would never see — so this diffs them."""
    world, _ = shop
    async with Session() as db:
        branch = await db.get(Branch, world.branch_id)
        # A usage-limited counter promotion, so the usage read runs too.
        db.add(
            Promotion(
                name="Limited",
                type="basic",
                trigger="spend",
                trigger_value=Decimal("0"),
                reward="percentage_off_order",
                reward_value=Decimal("5"),
                sources=["cashier"],
                auto_branch_ids=[world.branch_id],
                auto_apply=True,
                usage_limit=3,
            )
        )
        await db.flush()
        statements.clear()
        await counter_bundle_service.build_body(db, branch)
        await db.rollback()
    read = _tables(statements)
    # Usage is re-checked on every reuse rather than watched; the CTE is the
    # recursive menu walk over `menu_groups`.
    read -= {"orders", "order_discounts", "visible_groups"}
    assert read, "statements were captured"
    assert read <= counter_bundle_cache.BUNDLE_INPUT_TABLES, sorted(
        read - counter_bundle_cache.BUNDLE_INPUT_TABLES
    )
