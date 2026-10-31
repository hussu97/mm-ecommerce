"""
The register's order polls answer 304 when nothing changed — over HTTP against
the register app and a real Postgres, with Redis faked in memory.

What has to hold:

* a client that does not opt in (`X-POS-ETag: 1`) is served exactly as before:
  no ETag, no 304, the same bytes;
* an opted-in repeat poll with its ETag is a 304 decided before any order is
  read, and the ETag'd 200 body is the same bytes as the plain one;
* any committed ORM write to the order's tables at that branch retires the
  token — a line, a delivery, a bulk `update()` — and a write at another
  branch does not;
* a token past its expiry, or one served without Redis, is never honoured;
* the list's query-level loader options build byte-identical responses to the
  `selectinload`-everything options they replaced, in fewer round trips;
* every table the endpoint reads is one `pos_orders_etag` watches.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.v1 import pos_orders
from app.core.deps import get_db
from app.models import Order, OrderItem
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.order_delivery import OrderDelivery
from app.models.till import Till
from app.models.user import User
from app.pos_main import app as pos_app
from app.services.pos import pos_order_service, pos_orders_etag

from ._counter_world import attestation_for, build_world

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

LIST = "/api/v1/pos/orders"

#: Read by authentication, not by the order response.
_AUTH_TABLES = {"users", "roles", "user_roles", "role_permissions", "permissions"}


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
def Session(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def redis(monkeypatch):
    """An in-memory stand-in for the two cache calls the module makes."""
    store: dict[str, int] = {}
    state = {"up": True}

    async def cache_get(key):
        return store.get(key) if state["up"] else None

    async def cache_incr(key):
        if not state["up"]:
            return None
        store[key] = store.get(key, 0) + 1
        return store[key]

    monkeypatch.setattr(pos_orders_etag, "cache_get", cache_get)
    monkeypatch.setattr(pos_orders_etag, "cache_incr", cache_incr)
    return state


@pytest.fixture
def statements(engine):
    seen: list[str] = []

    def _record(_conn, _cursor, statement, *_args):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _record)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", _record)


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


async def _shop(Session, *, orders: int = 3):
    async with Session() as db:
        world = await build_world(db)
        cashier = await db.get(User, world.cashier_id)
        cashier.is_admin = True
        branch = await db.get(Branch, world.branch_id)
        till = await db.get(Till, world.till_id)
        ids = []
        for _ in range(orders):
            order = await pos_order_service.open_order(
                db, branch=branch, user=cashier, till=till
            )
            await pos_order_service.add_item(
                db, order=order, user=cashier, product_id=world.brownie_id
            )
            ids.append(order.id)
        await db.commit()
    await _settle()
    return world, ids


async def _settle() -> None:
    """Let the post-commit INCRs land."""
    for task in [
        t for t in asyncio.all_tasks() if t.get_name() == "pos-orders-generation"
    ]:
        await task


def _auth(world, **extra) -> dict:
    return {"Authorization": f"Bearer {attestation_for(world.cashier_id)}", **extra}


def _board(world) -> dict:
    return {
        "branch_id": str(world.branch_id),
        "business_date": world.business_date,
        "limit": "200",
    }


def _tables(sql: list[str]) -> set[str]:
    return {
        m.group(2)
        for s in sql
        for m in re.finditer(r"\b(FROM|JOIN|UPDATE|INTO)\s+\"?(\w+)", s)
    }


OPT_IN = {"X-POS-ETag": "1"}


async def test_a_client_that_does_not_opt_in_is_served_as_before(
    Session, client, redis
):
    world, _ = await _shop(Session)
    plain = await client.get(LIST, params=_board(world), headers=_auth(world))
    assert plain.status_code == 200
    assert "etag" not in plain.headers
    assert "cache-control" not in plain.headers

    # Even holding a real token: without the opt-in header nothing is compared.
    opted = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    again = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **{"If-None-Match": opted.headers["ETag"]}),
    )
    assert again.status_code == 200
    assert "etag" not in again.headers
    assert again.content == plain.content == opted.content


async def test_an_unchanged_poll_is_a_304_that_reads_no_order(
    Session, client, redis, statements
):
    world, _ = await _shop(Session)
    statements.clear()
    first = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    assert first.status_code == 200
    assert len(first.json()) == 3
    full = len(statements)

    statements.clear()
    again = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == first.headers["ETag"]
    assert not _tables(statements) - _AUTH_TABLES, "a 304 reads no order table"
    print(f"\nqueries per list poll: 200 {full}, 304 {len(statements)}")

    # A different query is a different answer.
    other = await client.get(
        LIST,
        params={"branch_id": str(world.branch_id), "open_only": "true"},
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert other.status_code == 200


async def test_a_write_at_the_branch_retires_the_token(Session, client, redis):
    world, ids = await _shop(Session)
    first = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))

    async with Session() as db:
        order = await pos_order_service.get_order(db, ids[0])
        await pos_order_service.add_item(
            db,
            order=order,
            user=await db.get(User, world.cashier_id),
            product_id=world.cookie_id,
        )
        await db.commit()
    await _settle()

    after = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert after.status_code == 200
    assert after.headers["ETag"] != first.headers["ETag"]
    lines = {o["id"]: len(o["items"]) for o in after.json()}
    assert lines[str(ids[0])] == 2


async def test_a_child_row_whose_order_is_not_in_the_session_bumps_every_branch(
    Session, client, redis
):
    world, ids = await _shop(Session)
    first = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    async with Session() as db:
        # A courier's driver push: the delivery row, its order never loaded.
        db.add(
            OrderDelivery(
                order_id=ids[1],
                provider="lalamove",
                driver_name="Rider",
                driver_phone="+971500000000",
            )
        )
        await db.commit()
    await _settle()
    after = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert after.status_code == 200
    row = next(o for o in after.json() if o["id"] == str(ids[1]))
    assert row["driver_name"] == "Rider"


async def test_a_bulk_update_retires_the_token(Session, client, redis):
    world, ids = await _shop(Session)
    first = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    async with Session() as db:
        await db.execute(
            update(OrderItem)
            .where(OrderItem.order_id == ids[2])
            .values(kitchen_notes="no nuts")
        )
        await db.commit()
    await _settle()
    after = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert after.status_code == 200


async def test_a_write_at_another_branch_keeps_the_token(Session, client, redis):
    world, _ = await _shop(Session)
    elsewhere, other_ids = await _shop(Session, orders=1)
    first = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    async with Session() as db:
        order = await pos_order_service.get_order(db, other_ids[0])
        await pos_order_service.add_item(
            db,
            order=order,
            user=await db.get(User, elsewhere.cashier_id),
            product_id=elsewhere.cookie_id,
        )
        await db.commit()
    await _settle()
    again = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert again.status_code == 304


async def test_an_expired_token_is_never_honoured(Session, client, redis, monkeypatch):
    world, _ = await _shop(Session)
    first = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    real = pos_orders_etag.time.time
    monkeypatch.setattr(
        pos_orders_etag.time, "time", lambda: real() + pos_orders_etag.TTL_CEILING + 1
    )
    after = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert after.status_code == 200


async def test_without_redis_there_is_no_etag_and_no_304(Session, client, redis):
    world, _ = await _shop(Session)
    first = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    redis["up"] = False
    down = await client.get(
        LIST,
        params=_board(world),
        headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert down.status_code == 200
    assert "etag" not in down.headers
    assert down.content == first.content


async def test_a_fresh_driver_position_shortens_the_token(Session, client, redis):
    world, ids = await _shop(Session)
    async with Session() as db:
        branch = await db.get(Branch, world.branch_id)
        branch.latitude, branch.longitude = Decimal("25.2"), Decimal("55.3")
        db.add(
            OrderDelivery(
                order_id=ids[0],
                provider="lalamove",
                driver_name="Rider",
                driver_latitude=Decimal("25.21"),
                driver_longitude=Decimal("55.31"),
                driver_location_at=utcnow() - timedelta(minutes=5),
            )
        )
        await db.commit()
    await _settle()
    resp = await client.get(LIST, params=_board(world), headers=_auth(world, **OPT_IN))
    until = int(resp.headers["ETag"].rsplit(".", 1)[1].rstrip('"'))
    # Five of the six minutes are gone: about one left, not five.
    assert until - utcnow().timestamp() < 90


async def test_the_detail_poll(Session, client, redis, statements):
    world, ids = await _shop(Session)
    path = f"{LIST}/{ids[0]}"
    plain = await client.get(path, headers=_auth(world))
    assert "etag" not in plain.headers

    first = await client.get(path, headers=_auth(world, **OPT_IN))
    assert first.content == plain.content
    statements.clear()
    again = await client.get(
        path, headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]})
    )
    assert again.status_code == 304
    assert _tables(statements) - _AUTH_TABLES == {"orders"}
    assert len([s for s in statements if "FROM orders" in s]) == 1
    print(f"\nqueries per detail poll: 304 {len(statements)}")

    async with Session() as db:
        db.add(OrderDelivery(order_id=ids[0], provider="lalamove", driver_name="R"))
        await db.commit()
    await _settle()
    after = await client.get(
        path, headers=_auth(world, **OPT_IN, **{"If-None-Match": first.headers["ETag"]})
    )
    assert after.status_code == 200
    assert after.json()["driver_name"] == "R"

    missing = await client.get(
        f"{LIST}/00000000-0000-0000-0000-000000000000",
        headers=_auth(world, **OPT_IN),
    )
    assert missing.status_code == 404


async def test_the_loader_options_build_the_same_bytes_in_fewer_trips(
    Session, client, redis, statements, monkeypatch
):
    world, ids = await _shop(Session)
    async with Session() as db:
        branch = await db.get(Branch, world.branch_id)
        branch.latitude, branch.longitude = Decimal("25.2"), Decimal("55.3")
        db.add(
            OrderDelivery(
                order_id=ids[0],
                provider="lalamove",
                driver_name="Rider",
                driver_latitude=Decimal("25.21"),
                driver_longitude=Decimal("55.31"),
                driver_location_at=utcnow(),
            )
        )
        await db.commit()

    statements.clear()
    new = await client.get(LIST, params=_board(world), headers=_auth(world))
    new_trips = len(statements)
    assert any(o["driver_distance_km"] for o in new.json()), "the branch is loaded"

    def previous():
        return (
            selectinload(Order.items),
            selectinload(Order.payments),
            selectinload(Order.order_charges),
            selectinload(Order.order_discounts),
            selectinload(Order.order_taxes),
            selectinload(Order.delivery),
            selectinload(Order.marketplace_return),
            selectinload(Order.branch),
        )

    monkeypatch.setattr(pos_orders, "_list_load_options", previous)
    statements.clear()
    old = await client.get(LIST, params=_board(world), headers=_auth(world))
    old_trips = len(statements)

    assert new.content == old.content
    print(f"\nqueries per list call: before {old_trips}, after {new_trips}")
    assert new_trips < old_trips


async def test_every_table_the_list_reads_is_watched(
    Session, client, redis, statements
):
    world, _ = await _shop(Session)
    statements.clear()
    for params in (
        _board(world),
        {"branch_id": str(world.branch_id), "open_only": "true"},
        {"branch_id": str(world.branch_id), "in_kitchen": "true"},
        {"branch_id": str(world.branch_id), "returns_pending": "true"},
    ):
        resp = await client.get(LIST, params=params, headers=_auth(world))
        assert resp.status_code == 200
    read = _tables(statements) - _AUTH_TABLES
    watched = pos_orders_etag.SCOPED_TABLES | pos_orders_etag.SHARED_TABLES
    assert read <= watched, f"unwatched: {read - watched}"
