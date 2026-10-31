"""The replenishment forecast against a real Postgres: facts, the form's endpoint,
and the shadow history.

A production branch and one destination sell a cookie (a product whose recipe
draws one) every day. The facts builder must count those sales by clock hour and
replay the production branch's stock from the ledger — including the afternoon
it ran out. The forecast endpoint must answer for the destination and for
production. The daily snapshot must store rows that are NOT linked to any order,
and the evaluator must match the day's actual transfer request back to them by
date, item and branch.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch, BranchWeeklyHours
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.operations import Transfer, TransferLine, TransferOrder
from app.models.order import DeliveryMethodEnum, Order, OrderItem
from app.models.product import Product
from app.models.replenishment import (
    ReplenishmentDailyFact,
    ReplenishmentForecast,
    ReplenishmentSettings,
)
from app.models.user import User
from app.schemas.replenishment import ForecastResponse
from app.services.inventory import inventory_service, recipe_service
from app.services.inventory.recipe_service import RecipeLineInput
from app.services.inventory.replenishment import (
    engine,
    facts,
    history,
    loaders,
    service,
)
from app.services.inventory.replenishment.settings import load_settings

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
]

MARKER = "replen-test"
TZ = ZoneInfo("Asia/Dubai")
TODAY = (
    datetime.now(TZ).date()
    if datetime.now(TZ).hour >= 4
    else (datetime.now(TZ).date() - timedelta(days=1))
)
#: The afternoon the production branch runs out (three days back).
OUT_DAY = TODAY - timedelta(days=3)


def _local(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(day, time(hh, mm), tzinfo=TZ).astimezone(timezone.utc)


class _Admin:
    def __init__(self):
        self.id = uuid.uuid4()
        self.email = f"{MARKER}@example.com"
        self.is_admin = True

    def can(self, _permission: str) -> bool:
        return True


@pytest.fixture
async def maker():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _post(db, branch_id, item_id, kind, qty, when, business_date):
    warehouse = await inventory_service.default_warehouse(db, branch_id)
    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(db, kind),
        type=kind,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch_id,
        warehouse_id=warehouse.id,
        business_date=business_date.isoformat(),
        occurred_at=when,
        items=[
            InventoryTransactionItem(
                item_id=item_id,
                quantity=Decimal(str(qty)),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=Decimal("1"),
            )
        ],
    )
    db.add(transaction)
    await db.flush()
    await inventory_service.post_transaction(db, transaction=transaction, user=None)


@pytest.fixture
async def world(maker):
    tag = uuid.uuid4().hex[:8]
    async with maker() as db:
        source = Branch(name=f"{MARKER} source {tag}", reference=f"{MARKER}-s-{tag}")
        dest = Branch(name=f"{MARKER} dest {tag}", reference=f"{MARKER}-d-{tag}")
        db.add_all([source, dest])
        await db.flush()
        for branch in (source, dest):
            db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
            db.add(
                BranchInventorySettings(
                    branch_id=branch.id,
                    inventory_enabled=True,
                    production_enabled=branch is source,
                    go_live_at=inventory_service.utcnow(),
                    go_live_sequence=0,
                )
            )
            for weekday in range(7):
                db.add(
                    BranchWeeklyHours(
                        branch_id=branch.id,
                        weekday=weekday,
                        opens="08:00",
                        closes="22:00",
                    )
                )
        user = User(email=f"{MARKER}-{tag}@example.com", hashed_password="x")
        cookie = InventoryItem(
            sku=f"{MARKER}-{tag}",
            name=f"{MARKER} Cookie {tag}",
            kind="produced_good",
            tracking_mode="stocked",
            storage_unit="piece",
            ingredient_unit="piece",
            storage_to_ingredient_factor=Decimal("1"),
        )
        flour = InventoryItem(
            sku=f"{MARKER}-flour-{tag}",
            name=f"{MARKER} Flour {tag}",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1"),
        )
        product = Product(name=f"{MARKER} Cookie", slug=f"{MARKER}-{tag}")
        db.add_all([user, cookie, flour, product])
        await db.flush()
        # The cookie is made at the source (so it gets a production forecast)…
        await recipe_service.draft_and_activate(
            db,
            kind="inventory_item",
            owner_id=cookie.id,
            lines=[RecipeLineInput(item_id=flour.id, quantity=Decimal("30"))],
            user_id=user.id,
        )
        # …and sold as a product that draws one.
        await recipe_service.draft_and_activate(
            db,
            kind="product",
            owner_id=product.id,
            lines=[RecipeLineInput(item_id=cookie.id, quantity=Decimal("1"))],
            user_id=user.id,
        )

        # 21 days of sales: 3 a day at the source, 2 at the destination, all at
        # 19:00 Dubai — completed counter sales.
        for offset in range(1, 22):
            day = TODAY - timedelta(days=offset)
            for branch, count in ((source, 3), (dest, 2)):
                for n in range(count):
                    order = Order(
                        order_number=f"RP-{tag}-{offset}-{n}-{str(branch.id)[:2]}",
                        email=f"{MARKER}@example.com",
                        source="cashier",
                        pos_status="closed",
                        is_pos=True,
                        branch_id=branch.id,
                        business_date=day.isoformat(),
                        delivery_method=DeliveryMethodEnum.PICKUP,
                        subtotal=Decimal("10"),
                        total=Decimal("10"),
                        created_at=_local(day, 19),
                        closed_at=_local(day, 19, 5),
                    )
                    db.add(order)
                    await db.flush()
                    db.add(
                        OrderItem(
                            order_id=order.id,
                            product_id=product.id,
                            product_name="Cookie",
                            product_sku="C",
                            quantity=1,
                            base_price=Decimal("10"),
                            unit_price=Decimal("10"),
                            total_price=Decimal("10"),
                        )
                    )

        # The source's stock is known from a count 10 days back; it runs out at
        # 15:00 on OUT_DAY and is restocked the next morning.
        count_day = TODAY - timedelta(days=10)
        await _post(
            db,
            source.id,
            cookie.id,
            InventoryTransactionTypeEnum.OPENING_BALANCE.value,
            40,
            _local(count_day, 6),
            count_day,
        )
        await _post(
            db,
            source.id,
            cookie.id,
            InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
            -40,
            _local(OUT_DAY, 15),
            OUT_DAY,
        )
        next_day = OUT_DAY + timedelta(days=1)
        await _post(
            db,
            source.id,
            cookie.id,
            InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
            40,
            _local(next_day, 6),
            next_day,
        )
        await db.commit()
        yield {"source": source, "dest": dest, "cookie": cookie, "product": product}

        settings = await load_settings(db)
        if settings.production_branch_id == source.id:
            settings.production_branch_id = None
        await db.execute(
            delete(ReplenishmentForecast).where(
                ReplenishmentForecast.item_id == cookie.id
            )
        )
        await db.execute(
            delete(ReplenishmentDailyFact).where(
                ReplenishmentDailyFact.item_id == cookie.id
            )
        )
        # Leave nothing another test would read: the orders (the VAT ledger test
        # sums every one) and the transfers are deleted; the product, items and
        # branches are retired, since recipes and closed ledger rows are
        # immutable by trigger.
        transfer_ids = select(Transfer.id).where(Transfer.source_branch_id == source.id)
        await db.execute(
            delete(TransferLine).where(TransferLine.transfer_id.in_(transfer_ids))
        )
        await db.execute(delete(Transfer).where(Transfer.source_branch_id == source.id))
        await db.execute(
            delete(TransferOrder).where(TransferOrder.source_branch_id == source.id)
        )
        await db.execute(delete(Order).where(Order.order_number.like(f"RP-{tag}-%")))
        # Recipes are immutable by trigger too: retire the product instead.
        await db.execute(
            update(Product).where(Product.id == product.id).values(is_active=False)
        )
        now = inventory_service.utcnow()
        await db.execute(
            update(InventoryItem)
            .where(InventoryItem.id.in_([cookie.id, flour.id]))
            .values(is_active=False, deleted_at=now)
        )
        await db.execute(
            update(Branch)
            .where(Branch.id.in_([source.id, dest.id]))
            .values(is_active=False, deleted_at=now)
        )
        await db.commit()


async def _build(maker, days: int = 22) -> None:
    async with maker() as db:
        ctx = await facts.load_context(db)
        for offset in range(days, 0, -1):
            await facts.build_day(db, TODAY - timedelta(days=offset), ctx)
        await db.commit()


async def test_facts_count_sales_by_hour_and_replay_the_stock_out(maker, world):
    await _build(maker)
    async with maker() as db:
        rows = {
            (row.branch_id, row.business_date): row
            for row in (
                await db.execute(
                    select(ReplenishmentDailyFact).where(
                        ReplenishmentDailyFact.item_id == world["cookie"].id
                    )
                )
            ).scalars()
        }
    source, dest = world["source"].id, world["dest"].id
    day = TODAY - timedelta(days=5)
    assert float(rows[(source, day)].sales_units) == 3
    assert float(rows[(dest, day)].sales_units) == 2
    assert float(rows[(source, day)].hourly_units[19]) == 3
    assert rows[(source, day)].hourly_open_minutes[8] == 60
    assert sum(rows[(source, day)].hourly_open_minutes) == 14 * 60

    # Stock known from the count on; unknown before it and at the destination.
    assert rows[(source, TODAY - timedelta(days=15))].hourly_in_stock_minutes is None
    assert rows[(dest, day)].hourly_in_stock_minutes is None
    assert (
        rows[(source, day)].hourly_in_stock_minutes
        == rows[(source, day)].hourly_open_minutes
    )

    out = rows[(source, OUT_DAY)].hourly_in_stock_minutes
    assert out[14] == 60 and out[15] == 0 and out[21] == 0
    assert float(rows[(source, OUT_DAY)].closing_on_hand) == 0


async def test_the_form_endpoint_answers_for_every_branch_and_production(maker, world):
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_current_active_user, get_db
    from app.main import app

    await _build(maker)

    async def override_get_db():
        async with maker() as session:
            yield session
            await session.commit()

    async def override_admin():
        return _Admin()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_admin
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            response = await client.get(
                "/api/v1/inventory/replenishment/forecast",
                params={"source_branch_id": str(world["source"].id)},
            )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["bucket_hours"] in (1, 2, 3, 4, 6)
    item = next(i for i in body["items"] if i["item_id"] == str(world["cookie"].id))
    lines = {line["branch_id"]: line for line in item["lines"]}
    assert lines[str(world["source"].id)]["kind"] == "retain"
    dest = lines[str(world["dest"].id)]
    assert dest["kind"] == "transfer"
    # Everything the source holds is accounted for, and never more.
    assert sum(line["qty"] for line in item["lines"]) <= max(item["source_on_hand"], 0)
    assert item["production"] is not None
    assert item["production"]["units"] >= 0


async def test_snapshot_is_unlinked_and_evaluation_matches_actuals_by_date(
    maker, world
):
    await _build(maker)
    source, dest, cookie = world["source"], world["dest"], world["cookie"]
    day = TODAY - timedelta(days=2)
    async with maker() as db:
        await db.execute(
            update(ReplenishmentSettings).values(production_branch_id=source.id)
        )
        await db.commit()
        written = await history.snapshot_day(db, as_of=_local(day, 9), mode="backtest")
        await db.commit()
        assert written > 0

        # What the admin actually raised that day: 5 cookies to the destination.
        order = TransferOrder(
            reference=f"TO-{uuid.uuid4().hex[:8]}",
            status="pending",
            kind="transfer",
            source_branch_id=source.id,
            business_date=day.isoformat(),
            required_date=day,
        )
        db.add(order)
        await db.flush()
        child = Transfer(
            transfer_order_id=order.id,
            reference=f"TR-{uuid.uuid4().hex[:8]}",
            status="pending",
            kind="transfer",
            branch_id=dest.id,
            source_branch_id=source.id,
            business_date=day.isoformat(),
        )
        db.add(child)
        await db.flush()
        db.add(
            TransferLine(
                transfer_id=child.id,
                item_id=cookie.id,
                quantity=Decimal("5"),
                unit="storage",
                conversion_factor=Decimal("1"),
            )
        )
        await db.commit()

        await history.evaluate_day(db, day)
        await db.commit()
        rows = {
            (row.kind, row.branch_id): row
            for row in (
                await db.execute(
                    select(ReplenishmentForecast).where(
                        ReplenishmentForecast.item_id == cookie.id,
                        ReplenishmentForecast.business_date == day,
                        ReplenishmentForecast.mode == "backtest",
                    )
                )
            ).scalars()
        }
    transfer = rows[("transfer", dest.id)]
    assert float(transfer.actual_requested_qty) == 5
    assert float(transfer.actual_sent_qty) == 0
    assert float(transfer.realized_sales) == 2
    assert transfer.evaluated_at is not None
    assert float(transfer.baseline_demand) == 2
    # The source kept what it held less what it was asked to send.
    retain = rows[("retain", source.id)]
    assert float(retain.actual_requested_qty) == max(
        0.0, float(retain.pool_at_snapshot) - 5
    )
    assert ("production", source.id) in rows


async def test_yesterdays_production_rows_do_not_keep_yesterday_pending(maker, world):
    """Yesterday's production rows are scored only once today is evaluated, so
    they must not make `tick` re-evaluate yesterday on every pass (it reloaded
    56 days of facts every ten minutes until the rollover)."""
    await _build(maker)
    source = world["source"]
    yesterday = TODAY - timedelta(days=1)
    async with maker() as db:
        await db.execute(
            update(ReplenishmentSettings).values(production_branch_id=source.id)
        )
        await db.commit()
        await history.snapshot_day(db, as_of=_local(yesterday, 9), mode="backtest")
        await db.commit()
        await history.evaluate_day(db, yesterday)
        await db.commit()

        left = (
            (
                await db.execute(
                    select(ReplenishmentForecast.kind).where(
                        ReplenishmentForecast.business_date == yesterday,
                        ReplenishmentForecast.evaluated_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert left and set(left) == {"production"}
        assert yesterday not in await history._pending_days(db, yesterday)
        # A day later the same rows are scoreable, and pending again.
        assert yesterday in await history._pending_days(db, TODAY)


async def _legacy_load_facts(db, start: date, end: date) -> list[engine.Fact]:
    """`loaders.load_facts` as it was: whole ORM entities, then converted."""
    rows = (
        (
            await db.execute(
                select(ReplenishmentDailyFact).where(
                    ReplenishmentDailyFact.business_date >= start,
                    ReplenishmentDailyFact.business_date < end,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        engine.Fact(
            branch_id=row.branch_id,
            item_id=row.item_id,
            business_date=row.business_date,
            sales_units=float(row.sales_units),
            hourly_units=tuple(float(u) for u in row.hourly_units),
            hourly_open_minutes=tuple(row.hourly_open_minutes),
            hourly_in_stock_minutes=(
                tuple(row.hourly_in_stock_minutes)
                if row.hourly_in_stock_minutes is not None
                else None
            ),
        )
        for row in rows
    ]


async def test_column_facts_and_threaded_engine_match_the_orm_path(maker, world):
    """The endpoint's speed-up (column select, engine in a thread) changes no
    output: the old path — ORM-entity facts, engine on the loop — over the same
    seeded world gives the identical response, field for field."""
    await _build(maker)
    as_of = datetime.now(timezone.utc)
    source = world["source"].id
    async with maker() as db:
        new = await service.current_forecast(db, source_branch_id=source, as_of=as_of)

        snapshot = await loaders.build_snapshot(
            db, as_of=as_of, source_branch_id=source
        )
        legacy_facts = await _legacy_load_facts(
            db,
            snapshot.business_date - timedelta(days=snapshot.settings.window_days),
            snapshot.business_date,
        )
        # The engine sees the very same facts, in the same order.
        assert snapshot.facts == legacy_facts
        assert any(f.item_id == world["cookie"].id for f in legacy_facts)

        legacy_snapshot = dataclasses.replace(snapshot, facts=legacy_facts)
        results = engine.forecast(legacy_snapshot)
        old = ForecastResponse(
            business_date=legacy_snapshot.business_date,
            as_of=as_of,
            source_branch_id=legacy_snapshot.pool_branch_id,
            bucket_hours=legacy_snapshot.settings.bucket_hours,
            service_level=legacy_snapshot.settings.service_level,
            algo_version=engine.ALGO_VERSION,
            history_days=len({f.business_date for f in legacy_snapshot.facts}),
            items=service.to_items(legacy_snapshot, results),
        )
    assert new.model_dump() == old.model_dump()
    cookie = next(i for i in new.items if i.item_id == world["cookie"].id)
    assert cookie.lines and cookie.production is not None


async def test_the_form_forecast_is_reused_per_branch_settings_and_bucket(
    maker, world, monkeypatch
):
    await _build(maker)
    source, dest = world["source"].id, world["dest"].id
    # One second into the current 10-minute bucket.
    bucket_start = int(datetime.now(timezone.utc).timestamp()) // 600 * 600
    clock = [datetime.fromtimestamp(bucket_start + 1, tz=timezone.utc)]
    monkeypatch.setattr(service, "_utcnow", lambda: clock[0])
    service._cache.clear()
    try:
        async with maker() as db:
            first = await service.current_forecast(db, source_branch_id=source)
            assert first.as_of == clock[0]
            # Same branch, settings and 10-minute bucket: the same answer.
            clock[0] += timedelta(minutes=9)
            again = await service.current_forecast(db, source_branch_id=source)
            assert again is first
            # A stock movement retires it at once, inside the bucket: a form
            # reopened right after a transfer must see the new on-hand.
            await _post(
                db,
                world["source"].id,
                world["cookie"].id,
                InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
                1,
                clock[0],
                TODAY,
            )
            await db.commit()
            moved = await service.current_forecast(db, source_branch_id=source)
            assert moved is not first
            first = moved
            # Another source branch is its own entry.
            other = await service.current_forecast(db, source_branch_id=dest)
            assert other is not first and other.source_branch_id == dest
            # A saved setting is a new key — reflected on the next open.
            row = await load_settings(db)
            row.half_life_days = row.half_life_days + 1
            await db.flush()
            changed = await service.current_forecast(db, source_branch_id=source)
            assert changed is not first
            await db.rollback()
            # The next bucket recomputes, and older buckets are dropped.
            clock[0] += timedelta(minutes=1)
            fresh = await service.current_forecast(db, source_branch_id=source)
            assert fresh is not first and fresh.as_of == clock[0]
            assert {key[3] for key in service._cache} == {
                int(clock[0].timestamp()) // service.CACHE_SECONDS
            }
            # An explicit as_of (backtest, shadow history) is never cached.
            explicit = await service.current_forecast(
                db, source_branch_id=source, as_of=clock[0]
            )
            assert explicit is not fresh
            assert explicit.model_dump() == fresh.model_dump()
    finally:
        service._cache.clear()
