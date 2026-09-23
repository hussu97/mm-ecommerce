"""
`GET /pos/counter/bundle`, `POST /pos/counter/sales` and the heartbeat/till
additions, over HTTP against the register app and a real Postgres.

What the register relies on:

* the bundle's `hash` is the sha256 of its canonical body and is stable across
  reads; the `ETag` answers `If-None-Match` with 304;
* the body is persisted on first serve (ingest re-prices against it);
* a terminal below `COUNTER_LOCAL_FIRST_MIN_BUILD` is told `off` whatever the
  branch flag says, and its sale sync is refused with 426;
* each terminal gets its own ticket prefix (`T1`, `T2`, …);
* the heartbeat's new body is optional; the till close's `device_pending_sales`
  is optional and refuses a close over unsynced sales.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.v1.devices import hash_device_token
from app.core.deps import get_current_active_user, get_db
from app.models.branch import Branch
from app.models.device import Device
from app.models.pos_counter import PosConfigBundle
from app.models.role import Role
from app.models.user import User
from app.pos_main import app as pos_app
from app.services.pos import counter_bundle_service

from ._counter_world import NEW_BUILD, build_world, device_sale

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

OLD_BUILD = "1056"


@pytest.fixture
async def Session():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


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


def _headers(token: str, build: str | None = NEW_BUILD, **extra) -> dict:
    headers = {
        "X-Device-Token": token,
        "X-App-Platform": "ios",
        "Content-Type": "application/json",
    }
    if build is not None:
        headers["X-App-Build"] = build
    headers.update(extra)
    return headers


async def test_the_bundle_is_hashed_etagged_persisted_and_304s(shop, client, Session):
    world, token = shop
    first = await client.get("/api/v1/pos/counter/bundle", headers=_headers(token))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["hash"] == counter_bundle_service.sha256_hex(body["bundle"])
    assert first.headers["ETag"].startswith(f'"{body["hash"]}.')
    assert first.headers["X-Counter-Bundle-Hash"] == body["hash"]

    bundle = body["bundle"]
    assert bundle["engine_version"] == 1
    assert bundle["branch"]["id"] == str(world.branch_id)
    assert {p["id"] for p in bundle["products"]} == {
        str(world.brownie_id),
        str(world.cookie_id),
    }
    brownie = next(p for p in bundle["products"] if p["id"] == str(world.brownie_id))
    assert brownie["base_price"] == "21.00"
    group = bundle["tax_groups"][0]
    assert group["resolved"]["rate"] == "0.0500"
    assert bundle["entity"]["vat_registered"] is True

    envelope = body["envelope"]
    assert envelope["counter_local_first"] == "on"
    assert envelope["ticket_prefix"] == "T1"
    assert envelope["last_ingested_ticket_seq"] == 0
    assert envelope["business_date"] == world.business_date

    again = await client.get(
        "/api/v1/pos/counter/bundle",
        headers=_headers(token, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert again.status_code == 304
    assert again.content == b""
    assert again.headers["ETag"] == first.headers["ETag"]

    async with Session() as db:
        stored = await db.get(PosConfigBundle, body["hash"])
        assert stored is not None and stored.branch_id == world.branch_id
        assert stored.payload == bundle


async def test_the_kill_switch_changes_the_etag(shop, client, Session):
    world, token = shop
    first = await client.get("/api/v1/pos/counter/bundle", headers=_headers(token))
    async with Session() as db:
        (await db.get(Branch, world.branch_id)).counter_local_first = "off"
        await db.commit()
    after = await client.get(
        "/api/v1/pos/counter/bundle",
        headers=_headers(token, **{"If-None-Match": first.headers["ETag"]}),
    )
    assert after.status_code == 200
    assert after.json()["envelope"]["counter_local_first"] == "off"
    assert after.json()["hash"] == first.json()["hash"], "pricing inputs unchanged"


async def test_an_old_build_is_told_off_and_cannot_sync(shop, client, Session):
    world, token = shop
    for build in (OLD_BUILD, None):
        resp = await client.get(
            "/api/v1/pos/counter/bundle", headers=_headers(token, build=build)
        )
        assert resp.status_code == 200
        envelope = resp.json()["envelope"]
        assert envelope["counter_local_first"] == "off"
        assert envelope["branch_counter_local_first"] == "on"
        assert envelope["build_supported"] is False

    async with Session() as db:
        sale = await device_sale(db, world, lines=[(world.brownie_id, 1)])
        await db.commit()
    refused = await client.post(
        "/api/v1/pos/counter/sales",
        headers=_headers(token, build=OLD_BUILD),
        content=sale.model_dump_json(),
    )
    assert refused.status_code == 426
    assert refused.json()["code"] == "upgrade_required"


async def test_a_sale_syncs_over_http_and_replays(shop, client, Session):
    world, token = shop
    async with Session() as db:
        sale = await device_sale(db, world, lines=[(world.brownie_id, 1)])
        await db.commit()
    first = await client.post(
        "/api/v1/pos/counter/sales",
        headers=_headers(token),
        content=sale.model_dump_json(),
    )
    assert first.status_code == 201, first.text
    assert first.json()["pricing_status"] == "verified"
    again = await client.post(
        "/api/v1/pos/counter/sales",
        headers=_headers(token),
        content=sale.model_dump_json(),
    )
    assert again.status_code == 200 and again.json()["status"] == "replayed"
    other = sale.model_copy(update={"notes": "changed"})
    conflict = await client.post(
        "/api/v1/pos/counter/sales",
        headers=_headers(token),
        content=other.model_dump_json(),
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "counter_sale_conflict"


async def test_each_terminal_gets_its_own_prefix(shop, client, Session):
    world, token = shop
    second_token = uuid.uuid4().hex
    async with Session() as db:
        db.add(
            Device(
                name="Second till",
                reference=f"D2{uuid.uuid4().hex[:8]}",
                type="cashier",
                branch_id=world.branch_id,
                status="used",
                token_hash=hash_device_token(second_token),
            )
        )
        await db.commit()
    one = await client.get("/api/v1/pos/counter/bundle", headers=_headers(token))
    two = await client.get("/api/v1/pos/counter/bundle", headers=_headers(second_token))
    assert one.json()["envelope"]["ticket_prefix"] == "T1"
    assert two.json()["envelope"]["ticket_prefix"] == "T2"
    assert one.json()["hash"] == two.json()["hash"], "one branch, one bundle"


async def test_the_heartbeat_body_is_optional_and_stores_counts(shop, client, Session):
    world, token = shop
    bare = await client.post("/api/v1/devices/heartbeat", headers=_headers(token))
    assert bare.status_code == 200, bare.text
    reported = await client.post(
        "/api/v1/devices/heartbeat",
        headers=_headers(token),
        json={
            "pending_sales": 3,
            "parked_sales": 1,
            "oldest_pending_at": "2026-09-23T08:00:00Z",
            "counter_mode": "local",
        },
    )
    assert reported.status_code == 200
    device = reported.json()["device"]
    assert device["pending_sales"] == 3 and device["parked_sales"] == 1
    assert device["counter_mode"] == "local"
    assert device["supports_local_first"] is True
    assert reported.json()["branch"]["counter_local_first"] == "on"


async def test_till_close_refuses_over_unsynced_sales_but_absent_means_zero(
    shop, client, Session
):
    world, token = shop
    async with Session() as db:
        role = Role(
            name=f"Till only {uuid.uuid4().hex[:8]}",
            permissions=["pos.register.access"],
        )
        db.add(role)
        await db.flush()
        (await db.get(User, world.cashier_id)).role_id = role.id
        await db.commit()
    async with Session() as db:
        cashier = await db.get(User, world.cashier_id)
        assert cashier.can("pos.register.access")
        assert not cashier.can("pos.till.manage")

    pos_app.dependency_overrides[get_current_active_user] = lambda: cashier
    try:
        refused = await client.post(
            f"/api/v1/tills/{world.till_id}/close",
            headers=_headers(token),
            json={"closing_amount": "100.00", "device_pending_sales": 2},
        )
        assert refused.status_code == 409, refused.text
        assert "not synced" in refused.json()["detail"]

        closed = await client.post(
            f"/api/v1/tills/{world.till_id}/close",
            headers=_headers(token),
            json={"closing_amount": "100.00"},
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
        assert Decimal(closed.json()["variance"]) == Decimal("0")
    finally:
        pos_app.dependency_overrides.pop(get_current_active_user, None)
