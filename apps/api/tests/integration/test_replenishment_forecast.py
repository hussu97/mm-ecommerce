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
from app.services.inventory import inventory_service, recipe_service
from app.services.inventory.recipe_service import RecipeLineInput
from app.services.inventory.replenishment import facts, history
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
