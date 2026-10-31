"""
The register's hot endpoints answer in a fixed number of statements, and the
cheaper versions answer exactly what the per-row ones did — over HTTP against
the POS app and a real Postgres.

The POS app runs on a five-connection request pool, so a round trip per branch,
per terminal or per count-sheet line is time every other till waits through.
What has to hold:

* the heartbeat writes the device row once, on the request's own connection —
  no side session — and still answers with what a fresh read of the row and the
  branch says; a heartbeat that fails still records that the terminal spoke;
* the branches and terminals dashboards cost the same number of statements
  however many branches and terminals there are, and agree field-for-field with
  the per-branch / per-terminal loops they replaced (kept below as references);
* the till-close tasks (create, refresh, ad hoc) and a transfer send read item
  levels in one statement, never `level_for` per line, and the count sheet
  carries the same expected quantities and the same level rows;
* claim-autoprint serialises the claimed transfers with one item lookup, to the
  same payload `_serialise_child` gives each one alone.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from app.api.v1 import operations
from app.api.v1.devices import hash_device_token
from app.core import database as app_database
from app.core.deps import get_db
from app.core.security import create_access_token
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.device import Device
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransactionItem,
    Warehouse,
)
from app.models.inventory_v2 import (
    InventoryReportTemplate,
    InventoryReportTemplateItem,
)
from app.models.operations import Transfer
from app.models.order import DeliveryMethodEnum, Order
from app.models.pos_order import PosOrderStatusEnum
from app.models.pos_table import PosTable, Section, TableStatusEnum
from app.models.till import Till, TillStatusEnum
from app.models.user import User
from app.pos_main import app as pos_app
from app.schemas.pos import BranchResponse, DeviceResponse
from app.services import crud_service
from app.services.inventory import inventory_service, transfer_service
from app.services.pos import business_day_service, busy_mode_service

from ._counter_world import build_world, purge_inventory
from ._stock import seed_stock

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

HEARTBEAT = "/api/v1/devices/heartbeat"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
def Session(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


class _Statements:
    """Every statement run on the request's engine (the test engine `get_db` is
    pointed at) and, separately, on the app's own engine — where a side session
    such as the `last_seen_at` stamp would run."""

    def __init__(self) -> None:
        self.request: list[str] = []
        self.side: list[str] = []

    def clear(self) -> None:
        self.request.clear()
        self.side.clear()

    def matching(self, prefix: str) -> list[str]:
        return [s for s in self.request if " ".join(s.split()).startswith(prefix)]


@pytest.fixture
def statements(engine):
    seen = _Statements()

    def _request(_conn, _cursor, statement, *_args):
        seen.request.append(statement)

    def _side(_conn, _cursor, statement, *_args):
        seen.side.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _request)
    event.listen(app_database.engine.sync_engine, "before_cursor_execute", _side)
    yield seen
    event.remove(engine.sync_engine, "before_cursor_execute", _request)
    event.remove(app_database.engine.sync_engine, "before_cursor_execute", _side)


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
    yield AsyncClient(
        transport=ASGITransport(app=pos_app, raise_app_exceptions=False),
        base_url="http://pos",
    )
    pos_app.dependency_overrides.clear()


def _pos_order(world, *, pos_status, total):
    closed = pos_status == PosOrderStatusEnum.CLOSED.value
    return Order(
        order_number=f"QF-{uuid.uuid4().hex[:14]}",
        email="pytest-fanout@example.com",
        source="cashier",
        branch_id=world.branch_id,
        device_id=world.device_id,
        is_pos=True,
        business_date=world.business_date,
        status="delivered" if closed else "confirmed",
        pos_status=pos_status,
        closed_at=utcnow() if closed else None,
        opened_at=utcnow() - timedelta(minutes=5),
        delivery_method=DeliveryMethodEnum.PICKUP,
        subtotal=Decimal(total),
        total=Decimal(total),
    )


def _item(n: int, *, active: bool = True) -> InventoryItem:
    return InventoryItem(
        sku=f"QF-{uuid.uuid4().hex[:10]}",
        name=f"Fanout item {n}",
        kind="raw_material",
        tracking_mode="stocked",
        storage_unit="g",
        ingredient_unit="g",
        storage_to_ingredient_factor=Decimal("1"),
        is_active=active,
    )


@pytest.fixture
async def shop(Session):
    """Two branches with a terminal and an open till each, checks on the first
    (two open, one closed), a floor with one occupied table, and a count
    template of six live items plus one since deactivated."""
    token = uuid.uuid4().hex
    async with Session() as db:
        world = await build_world(db, inventory=True)
        other = await build_world(db)
        device = await db.get(Device, world.device_id)
        device.token_hash = hash_device_token(token)
        admin = User(
            email=f"fanout-admin-{uuid.uuid4().hex[:8]}@example.com",
            hashed_password="x",
            is_staff=True,
            is_admin=True,
            is_active=True,
        )
        db.add(admin)
        for status_, total in (
            ("active", "10.00"),
            ("active", "5.50"),
            ("closed", "20.25"),
        ):
            db.add(_pos_order(world, pos_status=status_, total=total))
        section = Section(branch_id=world.branch_id, name="Floor")
        db.add(section)
        await db.flush()
        db.add_all(
            [
                PosTable(
                    section_id=section.id,
                    name="T1",
                    status=TableStatusEnum.OCCUPIED.value,
                ),
                PosTable(section_id=section.id, name="T2"),
            ]
        )
        items = [_item(n) for n in range(6)]
        retired = _item(99, active=False)
        db.add_all([*items, retired])
        await db.flush()
        template = InventoryReportTemplate(
            branch_id=world.branch_id,
            name="Fanout count",
            report_type="raw_materials",
            cadence="per_till",
            version_number=1,
            configuration={},
        )
        db.add(template)
        await db.flush()
        for n, item in enumerate([*items, retired]):
            db.add(
                InventoryReportTemplateItem(
                    template_id=template.id, item_id=item.id, display_order=n
                )
            )
        await db.commit()
        ids = SimpleNamespace(
            world=world,
            other=other,
            token=token,
            admin_id=admin.id,
            admin_email=admin.email,
            item_ids=[i.id for i in items],
            retired_id=retired.id,
        )
    yield ids
    await purge_inventory(Session, world)


def _device_headers(token: str, build: str = "1072") -> dict:
    return {"X-Device-Token": token, "X-App-Platform": "ios", "X-App-Build": build}


def _user_headers(shop) -> dict:
    jwt = create_access_token(
        user_id=str(shop.admin_id),
        email=shop.admin_email,
        is_admin=True,
        expires_delta=timedelta(hours=1),
    )
    return {"Authorization": f"Bearer {jwt}"}


async def _default_warehouse_id(db, branch_id) -> uuid.UUID:
    return (
        await db.execute(
            select(Warehouse.id).where(
                Warehouse.branch_id == branch_id, Warehouse.is_default.is_(True)
            )
        )
    ).scalar_one()


async def _fresh_session_reply(Session, device_id, branch_id) -> dict:
    """What the heartbeat answered before: the device and branch re-read."""
    async with Session() as db:
        device = await db.get(Device, device_id)
        branch = await db.get(Branch, branch_id)
        return {
            "device": DeviceResponse.model_validate(device).model_dump(mode="json"),
            "branch": BranchResponse.model_validate(branch).model_dump(mode="json"),
        }


# ─── Heartbeat ────────────────────────────────────────────────────────────────


async def test_a_stale_heartbeat_writes_once_on_the_request_connection(
    shop, client, statements, Session
):
    world = shop.world
    async with Session() as db:
        (await db.get(Device, world.device_id)).last_seen_at = None
        await db.commit()

    statements.clear()
    before = utcnow()
    resp = await client.post(HEARTBEAT, headers=_device_headers(shop.token, "2001"))
    assert resp.status_code == 200, resp.text

    # Was: a side-session UPDATE on a second pool connection, the request's own
    # UPDATE, a refresh SELECT and the branch with its four eager maps (11).
    assert statements.side == []
    assert len(statements.matching("UPDATE devices")) == 1
    assert len(statements.request) <= 5, statements.request

    body = resp.json()
    fresh = await _fresh_session_reply(Session, world.device_id, world.branch_id)
    assert body["device"] == fresh["device"]
    assert body["branch"] == fresh["branch"]
    assert body["device"]["build_number"] == "2001"
    assert body["device"]["platform"] == "ios"
    assert datetime.fromisoformat(body["device"]["last_seen_at"]) >= before


async def test_a_fresh_heartbeat_is_as_cheap(shop, client, statements, Session):
    world = shop.world
    await client.post(HEARTBEAT, headers=_device_headers(shop.token))
    statements.clear()
    resp = await client.post(HEARTBEAT, headers=_device_headers(shop.token))
    assert resp.status_code == 200, resp.text
    assert statements.side == []
    assert len(statements.request) <= 5, statements.request
    fresh = await _fresh_session_reply(Session, world.device_id, world.branch_id)
    assert resp.json()["device"] == fresh["device"]


async def test_a_reported_instant_comes_back_as_the_database_holds_it(
    shop, client, Session
):
    """A client-sent `oldest_pending_at` in its own offset is answered in UTC,
    exactly as a re-read of the row gives it."""
    world = shop.world
    gulf = timezone(timedelta(hours=4))
    sent = datetime(2026, 10, 31, 14, 30, 5, 123456, tzinfo=gulf)
    resp = await client.post(
        HEARTBEAT,
        headers=_device_headers(shop.token),
        json={
            "pending_sales": 2,
            "parked_sales": 1,
            "oldest_pending_at": sent.isoformat(),
            "counter_mode": "local",
        },
    )
    assert resp.status_code == 200, resp.text
    device = resp.json()["device"]
    fresh = await _fresh_session_reply(Session, world.device_id, world.branch_id)
    assert device == fresh["device"]
    assert datetime.fromisoformat(device["oldest_pending_sale_at"]) == sent
    assert device["pending_sales"] == 2 and device["counter_mode"] == "local"


async def test_a_failed_heartbeat_still_records_that_the_terminal_spoke(
    shop, client, Session, monkeypatch
):
    world = shop.world
    async with Session() as db:
        (await db.get(Device, world.device_id)).last_seen_at = None
        await db.commit()
    # The stamp runs on the app's own module-level engine; drop any connection
    # an earlier test pooled on its (now closed) event loop.
    await app_database.engine.dispose()

    async def broken(*_args, **_kwargs):
        raise RuntimeError("busy-mode read failed")

    monkeypatch.setattr(busy_mode_service, "status", broken)
    resp = await client.post(HEARTBEAT, headers=_device_headers(shop.token, "3003"))
    assert resp.status_code == 500

    async with Session() as db:
        device = await db.get(Device, world.device_id)
        assert device.last_seen_at is not None
        assert device.build_number == "3003"


# ─── Dashboards ───────────────────────────────────────────────────────────────


async def _reference_branches_dashboard(db) -> dict:
    """The per-branch loop `branches_dashboard` ran before it was grouped."""
    branches = await crud_service.list_all(db, Branch, include_inactive=False)
    rows = []
    now = utcnow()
    for branch in branches:
        active = (
            await db.execute(
                select(
                    func.count(Order.id), func.coalesce(func.sum(Order.total), 0)
                ).where(
                    Order.is_pos.is_(True),
                    Order.branch_id == branch.id,
                    Order.pos_status == PosOrderStatusEnum.ACTIVE.value,
                )
            )
        ).one()
        tables = (
            await db.execute(
                select(
                    func.count(PosTable.id),
                    func.count(PosTable.id).filter(
                        PosTable.status == TableStatusEnum.OCCUPIED.value
                    ),
                )
                .select_from(PosTable)
                .join(Section, Section.id == PosTable.section_id)
                .where(Section.branch_id == branch.id, PosTable.deleted_at.is_(None))
            )
        ).one()
        open_tills = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Till)
                    .where(
                        Till.branch_id == branch.id,
                        Till.status == TillStatusEnum.OPEN.value,
                    )
                )
            ).scalar_one()
        )
        devices = list(
            (
                await db.execute(
                    select(Device).where(
                        Device.branch_id == branch.id,
                        Device.deleted_at.is_(None),
                        Device.type.in_(["cashier", "sub_cashier"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        offline = sum(
            1
            for d in devices
            if d.last_seen_at is None
            or (now - d.last_seen_at).total_seconds() > operations.OFFLINE_AFTER_SECONDS
        )
        last_seen = max(
            (d.last_seen_at for d in devices if d.last_seen_at), default=None
        )
        last_order = (
            await db.execute(
                select(func.max(Order.opened_at)).where(
                    Order.is_pos.is_(True), Order.branch_id == branch.id
                )
            )
        ).scalar_one_or_none()
        rows.append(
            operations.BranchLive(
                branch_id=branch.id,
                branch_name=branch.name,
                reference=branch.reference,
                active_orders=int(active[0] or 0),
                active_orders_amount=Decimal(str(active[1] or 0)),
                total_tables=int(tables[0] or 0),
                occupied_tables=int(tables[1] or 0),
                open_tills=open_tills,
                total_devices=len(devices),
                offline_devices=offline,
                last_order_at=last_order,
                last_device_seen_at=last_seen,
            )
        )
    return operations.BranchesDashboard(
        active_orders=sum(r.active_orders for r in rows),
        active_orders_amount=sum(
            (r.active_orders_amount for r in rows), start=Decimal("0")
        ),
        occupied_tables=sum(r.occupied_tables for r in rows),
        offline_devices=sum(r.offline_devices for r in rows),
        branches=rows,
    ).model_dump(mode="json")


async def _reference_terminals_dashboard(db, branch_id=None) -> dict:
    """The per-terminal loop `terminals_dashboard` ran before it was grouped."""
    branch_filters = [Branch.is_active.is_(True), Branch.deleted_at.is_(None)]
    if branch_id:
        branch_filters.append(Branch.id == branch_id)
    branches = list(
        (await db.execute(select(Branch).where(*branch_filters))).scalars().all()
    )
    branch_names = {b.id: b.name for b in branches}
    devices = list(
        (
            await db.execute(
                select(Device)
                .where(
                    Device.branch_id.in_(list(branch_names) or [uuid.uuid4()]),
                    Device.deleted_at.is_(None),
                    Device.type.in_(["cashier", "sub_cashier"]),
                )
                .order_by(Device.name.asc())
            )
        )
        .scalars()
        .all()
    )
    business_dates = {
        b.id: await business_day_service.current_business_date(db, b) for b in branches
    }
    now = utcnow()
    rows = []
    for device in devices:
        active = (
            await db.execute(
                select(
                    func.count(Order.id), func.coalesce(func.sum(Order.total), 0)
                ).where(
                    Order.is_pos.is_(True),
                    Order.device_id == device.id,
                    Order.pos_status == PosOrderStatusEnum.ACTIVE.value,
                )
            )
        ).one()
        today = (
            await db.execute(
                select(
                    func.count(Order.id), func.coalesce(func.sum(Order.total), 0)
                ).where(
                    Order.is_pos.is_(True),
                    Order.device_id == device.id,
                    Order.business_date == business_dates.get(device.branch_id),
                    Order.pos_status == PosOrderStatusEnum.CLOSED.value,
                )
            )
        ).one()
        till = (
            await db.execute(
                select(Till)
                .where(
                    Till.device_id == device.id,
                    Till.status == TillStatusEnum.OPEN.value,
                )
                .order_by(Till.opened_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        till_user = None
        if till is not None:
            operator = await db.get(User, till.user_id)
            if operator is not None:
                till_user = operator.display_name or operator.email
        rows.append(
            operations.TerminalLive(
                device_id=device.id,
                name=device.name,
                reference=device.reference,
                type=device.type,
                status=device.status,
                branch_id=device.branch_id,
                branch_name=branch_names.get(device.branch_id, "Unknown"),
                last_seen_at=device.last_seen_at,
                is_online=device.last_seen_at is not None
                and (now - device.last_seen_at).total_seconds()
                <= operations.OFFLINE_AFTER_SECONDS,
                app_version=device.app_version,
                build_number=device.build_number,
                os_version=device.os_version,
                model_identifier=device.model_identifier,
                counter_mode=device.counter_mode,
                ticket_prefix=device.ticket_prefix,
                supports_local_first=operations.build_at_least(
                    device.build_number, operations.COUNTER_LOCAL_FIRST_MIN_BUILD
                ),
                pending_sales=device.pending_sales,
                parked_sales=device.parked_sales,
                oldest_pending_sale_at=device.oldest_pending_sale_at,
                open_till_id=till.id if till else None,
                open_till_user=till_user,
                open_till_opened_at=till.opened_at if till else None,
                active_orders=int(active[0] or 0),
                active_orders_amount=Decimal(str(active[1] or 0)),
                orders_today=int(today[0] or 0),
                sales_today=Decimal(str(today[1] or 0)),
            )
        )
    return operations.TerminalsDashboard(
        terminals=len(rows),
        online=sum(1 for r in rows if r.is_online),
        offline=sum(1 for r in rows if not r.is_online),
        open_tills=sum(1 for r in rows if r.open_till_id is not None),
        orders_today=sum(r.orders_today for r in rows),
        sales_today=sum((r.sales_today for r in rows), start=Decimal("0")),
        devices=rows,
    ).model_dump(mode="json")


async def test_the_branches_dashboard_is_grouped_and_unchanged(
    shop, client, statements, Session
):
    # The terminal online now, so the offline count is not on a boundary.
    await client.post(HEARTBEAT, headers=_device_headers(shop.token))
    statements.clear()
    resp = await client.get(
        "/api/v1/pos/dashboard/branches", headers=_user_headers(shop)
    )
    assert resp.status_code == 200, resp.text
    # Every branch in the database is on this dashboard; the statement count
    # must not grow with them (it was 1 + 4 eager + 5 per branch).
    assert len(statements.request) <= 7, len(statements.request)

    async with Session() as db:
        expected = await _reference_branches_dashboard(db)
    assert resp.json() == expected
    mine = next(
        b
        for b in resp.json()["branches"]
        if b["branch_id"] == str(shop.world.branch_id)
    )
    assert mine["active_orders"] == 2
    assert Decimal(mine["active_orders_amount"]) == Decimal("15.50")
    assert (mine["total_tables"], mine["occupied_tables"]) == (2, 1)
    assert mine["open_tills"] == 1 and mine["total_devices"] == 1


@pytest.mark.parametrize("scoped", [False, True])
async def test_the_terminals_dashboard_is_grouped_and_unchanged(
    shop, client, statements, Session, scoped
):
    await client.post(HEARTBEAT, headers=_device_headers(shop.token))
    path = "/api/v1/pos/dashboard/devices"
    if scoped:
        path += f"?branch_id={shop.world.branch_id}"
    statements.clear()
    resp = await client.get(path, headers=_user_headers(shop))
    assert resp.status_code == 200, resp.text
    # Was 3-4 per terminal; now a fixed handful whatever the estate's size.
    assert len(statements.request) <= 8, len(statements.request)

    async with Session() as db:
        expected = await _reference_terminals_dashboard(
            db, shop.world.branch_id if scoped else None
        )
    assert resp.json() == expected
    mine = next(
        d for d in resp.json()["devices"] if d["device_id"] == str(shop.world.device_id)
    )
    assert mine["active_orders"] == 2 and mine["orders_today"] == 1
    assert Decimal(mine["sales_today"]) == Decimal("20.25")
    assert mine["open_till_id"] == str(shop.world.till_id)
    assert mine["open_till_user"] is not None


# ─── Report labels ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("dimension", ["branch", "device"])
async def test_report_labels_read_names_alone(
    shop, client, statements, Session, dimension
):
    statements.clear()
    resp = await client.get(
        f"/api/v1/pos/reports/sales/by?dimension={dimension}",
        headers=_user_headers(shop),
    )
    assert resp.status_code == 200, resp.text
    # Auth, the grouped query, the label lookup — no eager relationships.
    assert len(statements.request) <= 3, statements.request
    async with Session() as db:
        model = Branch if dimension == "branch" else Device
        names = dict((await db.execute(select(model.id, model.name))).all())
    rows = [row for row in resp.json() if row["key"] != "unknown"]
    assert rows
    for row in rows:
        assert row["label"] == names[uuid.UUID(row["key"])]


# ─── Till-close count sheets ──────────────────────────────────────────────────


@pytest.fixture
def per_line_level_reads(monkeypatch):
    """`level_for` calls that only read (the mutation path locks, and is not
    what these endpoints were doing per line)."""
    calls: list[uuid.UUID] = []
    real = inventory_service.level_for

    async def spy(db, item_id, warehouse_id, *, for_update=False):
        if not for_update:
            calls.append(item_id)
        return await real(db, item_id, warehouse_id, for_update=for_update)

    monkeypatch.setattr(inventory_service, "level_for", spy)
    return calls


async def _lines_by_item(Session, report_id) -> dict:
    from app.services.inventory import report_service

    async with Session() as db:
        report = await report_service.load_report(db, report_id)
        return {line.item_id: line for line in report.lines}


async def test_the_count_sheet_reads_levels_once_and_matches_them(
    shop, client, statements, Session, per_line_level_reads
):
    world = shop.world
    tasks = f"/api/v1/pos/inventory/tasks?till_id={world.till_id}"
    async with Session() as db:
        wh = await _default_warehouse_id(db, world.branch_id)
        # One item already has stock; the rest have no level row yet.
        await seed_stock(
            db,
            branch_id=world.branch_id,
            warehouse_id=wh,
            item_id=shop.item_ids[1],
            quantity="4",
            unit_cost="2",
        )
        await db.commit()

    # Create.
    statements.clear()
    resp = await client.get(tasks, headers=_user_headers(shop))
    assert resp.status_code == 200, resp.text
    [report] = resp.json()["reports"]
    assert per_line_level_reads == []
    assert len(statements.matching("SELECT inventory_levels")) == 1
    created = len(statements.request)

    lines = await _lines_by_item(Session, uuid.UUID(report["id"]))
    # The deactivated item is left off the sheet and given no level row.
    assert set(lines) == set(shop.item_ids)
    async with Session() as db:
        levels = {
            level.item_id: level
            for level in (
                await db.execute(
                    select(InventoryLevel).where(InventoryLevel.warehouse_id == wh)
                )
            ).scalars()
        }
    assert set(levels) >= set(shop.item_ids)
    assert shop.retired_id not in levels
    for item_id, line in lines.items():
        assert Decimal(str(line.expected_quantity)) == Decimal(
            str(levels[item_id].quantity)
        )
    assert Decimal(str(lines[shop.item_ids[1]].expected_quantity)) == Decimal("4")

    # Refresh: a movement since the sheet was prefilled.
    async with Session() as db:
        await seed_stock(
            db,
            branch_id=world.branch_id,
            warehouse_id=wh,
            item_id=shop.item_ids[0],
            quantity="7",
            unit_cost="2",
        )
        await db.commit()
    statements.clear()
    resp = await client.get(tasks, headers=_user_headers(shop))
    assert resp.status_code == 200, resp.text
    assert per_line_level_reads == []
    assert len(statements.matching("SELECT inventory_levels")) == 1
    lines = await _lines_by_item(Session, uuid.UUID(report["id"]))
    assert Decimal(str(lines[shop.item_ids[0]].expected_quantity)) == Decimal("7")
    assert Decimal(str(lines[shop.item_ids[1]].expected_quantity)) == Decimal("4")

    # Ad hoc on a later close: a fresh sheet, the same single read.
    statements.clear()
    resp = await client.post(
        f"/api/v1/pos/inventory/tasks/adhoc?till_id={world.till_id}",
        headers=_user_headers(shop),
    )
    assert resp.status_code == 200, resp.text
    assert per_line_level_reads == []
    assert len(statements.matching("SELECT inventory_levels")) == 1
    # A sheet of six lines used to cost two reads and a flush per line on top.
    assert created <= 36, created


async def test_levels_for_is_level_for_in_one_read(shop, Session):
    world = shop.world
    async with Session() as db:
        wh = await _default_warehouse_id(db, world.branch_id)
        await seed_stock(
            db,
            branch_id=world.branch_id,
            warehouse_id=wh,
            item_id=shop.item_ids[2],
            quantity="3",
        )
        await db.commit()

    wanted = [shop.item_ids[2], shop.item_ids[3], shop.item_ids[2]]
    async with Session() as db:
        batched = await inventory_service.levels_for(db, wanted, wh)
        assert set(batched) == {shop.item_ids[2], shop.item_ids[3]}
        for item_id in set(wanted):
            assert (
                await inventory_service.level_for(db, item_id, wh) is batched[item_id]
            )
        assert Decimal(str(batched[shop.item_ids[2]].quantity)) == Decimal("3")
        created = batched[shop.item_ids[3]]
        assert (Decimal(str(created.quantity)), Decimal(str(created.average_cost))) == (
            Decimal("0"),
            Decimal("0"),
        )
        assert await inventory_service.levels_for(db, [], wh) == {}
        await db.rollback()


# ─── Transfers ────────────────────────────────────────────────────────────────


async def _raise_transfers(
    Session, shop, *, lines_each: int, orders: int, override: bool = True
) -> None:
    async with Session() as db:
        source = await db.get(Branch, shop.world.branch_id)
        admin = await db.get(User, shop.admin_id)
        for n in range(orders):
            await transfer_service.create_transfer_order(
                db,
                source_branch=source,
                user=admin,
                items=[
                    SimpleNamespace(
                        item_id=item_id,
                        unit="storage",
                        override=override,
                        allocations=[
                            SimpleNamespace(
                                branch_id=shop.other.branch_id, quantity=Decimal("2")
                            )
                        ],
                    )
                    for item_id in shop.item_ids[n : n + lines_each]
                ],
            )
        await db.commit()


async def test_claim_autoprint_serialises_the_batch_once(
    shop, client, statements, Session
):
    await _raise_transfers(Session, shop, lines_each=2, orders=3)
    statements.clear()
    resp = await client.post(
        "/api/v1/pos/inventory/transfers/claim-autoprint"
        f"?source_branch_id={shop.world.branch_id}",
        headers=_user_headers(shop),
    )
    assert resp.status_code == 200, resp.text
    claimed = resp.json()
    assert len(claimed) == 3
    # One item read for all three transfers (was one, plus categories, each).
    assert len(statements.matching("SELECT inventory_items.sku")) == 1
    assert len(statements.request) <= 8, len(statements.request)

    async with Session() as db:
        rows = list(
            (
                await db.execute(
                    select(Transfer)
                    .where(Transfer.id.in_([uuid.UUID(t["id"]) for t in claimed]))
                    .options(selectinload(Transfer.items))
                    .order_by(Transfer.created_at.desc())
                )
            )
            .scalars()
            .unique()
            .all()
        )
        alone = [
            (await operations._serialise_child(db, t)).model_dump(mode="json")
            for t in rows
        ]
    assert claimed == alone
    assert all(line["item_name"] for t in claimed for line in t["items"])


async def test_a_transfer_send_reads_its_lines_levels_once(
    shop, client, statements, Session, per_line_level_reads
):
    async with Session() as db:
        wh = await _default_warehouse_id(db, shop.world.branch_id)
        for item_id in shop.item_ids[:4]:
            await seed_stock(
                db,
                branch_id=shop.world.branch_id,
                warehouse_id=wh,
                item_id=item_id,
                quantity="10",
                unit_cost="3",
            )
        await db.commit()
    await _raise_transfers(Session, shop, lines_each=4, orders=1, override=False)
    async with Session() as db:
        transfer_id = (
            await db.execute(
                select(Transfer.id).where(
                    Transfer.source_branch_id == shop.world.branch_id
                )
            )
        ).scalar_one()
    statements.clear()
    per_line_level_reads.clear()  # raising the order reads levels; not ours
    resp = await client.post(
        f"/api/v1/pos/inventory/transfers/{transfer_id}/send",
        headers=_user_headers(shop),
    )
    assert resp.status_code == 200, resp.text
    # Before posting, the four lines' items and source levels were read one by
    # one; now one read each. (The ledger posting itself is unchanged.)
    assert per_line_level_reads == []
    body = resp.json()
    assert body["status"] == "sent"
    assert [Decimal(line["sent_quantity"]) for line in body["items"]] == [
        Decimal("2")
    ] * 4
    assert all(line["item_name"] for line in body["items"])
    # Each line left at the source's moving average, read off the batched level.
    async with Session() as db:
        transfer = await db.get(Transfer, transfer_id)
        costs = (
            (
                await db.execute(
                    select(InventoryTransactionItem.unit_cost).where(
                        InventoryTransactionItem.transaction_id
                        == transfer.sent_transaction_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert sorted(Decimal(str(c)) for c in costs) == [Decimal("3")] * 4
