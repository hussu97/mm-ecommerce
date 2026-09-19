"""The VAT ledger cache, recomputed from source against a real Postgres.

compute_window rebuilds vat_ledger_entries from orders (output VAT and refunds),
their fee columns and order deliveries (input VAT, VAT-inclusive), and received
purchase orders (raw-goods input VAT). A non-registered legal entity's input rows
keep the cost visible but zero the VAT and flag it non-recoverable; its output VAT
is already zero on the order. A mocked session cannot answer this — it is which
rows the aggregation keeps and how each splits net vs VAT.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Branch, LegalEntity, Order
from app.models.inventory import PurchaseOrder, Supplier, Warehouse
from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.models.vat_ledger import VatCategoryEnum, VatDirectionEnum, VatLedgerEntry
from app.services import vat_ledger

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "vat-ledger-test"
BDATE = "2026-09-15"


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


def _order(branch_id, entity_id, *, source, status, pos_status=None, **money):
    closed_at = (
        datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)
        if pos_status == "closed"
        else None
    )
    return Order(
        order_number=f"VL-{uuid.uuid4().hex[:14]}",
        email="vat@example.com",
        source=source,
        branch_id=branch_id,
        legal_entity_id=entity_id,
        is_pos=True,
        business_date=BDATE,
        status=status,
        pos_status=pos_status,
        closed_at=closed_at,
        delivery_method=DeliveryMethodEnum.DELIVERY,
        subtotal=money.get("total", Decimal("0")),
        total=money.get("total", Decimal("0")),
        total_excl_vat=money.get("total_excl_vat"),
        vat_amount=money.get("vat_amount"),
        vat_rate=money.get("vat_rate"),
        refunded_amount=money.get("refunded_amount"),
        aggregator_fee=money.get("aggregator_fee"),
        payment_fee=money.get("payment_fee"),
    )


@pytest.fixture
async def seeded(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        # The legal entities are seeded by migration 237 — use them, don't clash on
        # the unique reference.
        fatema = (
            await db.execute(
                select(LegalEntity).where(LegalEntity.reference == "fatema")
            )
        ).scalar_one()
        najm = (
            await db.execute(
                select(LegalEntity)
                .where(LegalEntity.vat_registered.is_(False))
                .limit(1)
            )
        ).scalar_one()
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:8]}"
        )
        db.add(branch)
        await db.flush()
        db.add(Warehouse(branch_id=branch.id, name="Default stock", is_default=True))

        # Fatema (registered)
        o1 = _order(  # counter sale → sales_output
            branch.id,
            fatema.id,
            source="cashier",
            status=OrderStatusEnum.DELIVERED.value,
            pos_status="closed",
            total=Decimal("105"),
            total_excl_vat=Decimal("100"),
            vat_amount=Decimal("5"),
            vat_rate=Decimal("0.05"),
        )
        o2 = _order(  # refunded → sales_refund only (not a completed sale)
            branch.id,
            fatema.id,
            source="online",
            status=OrderStatusEnum.REFUNDED.value,
            total=Decimal("105"),
            vat_rate=Decimal("0.05"),
            refunded_amount=Decimal("21"),
        )
        o3 = _order(  # cancelled but carries fees → commission + payment only
            branch.id,
            fatema.id,
            source="aggregator",
            status=OrderStatusEnum.CANCELLED.value,
            total=Decimal("0"),
            aggregator_fee=Decimal("10.50"),
            payment_fee=Decimal("2.10"),
        )
        o4 = _order(  # cancelled, carries a courier cost → courier_fees only
            branch.id,
            fatema.id,
            source="online",
            status=OrderStatusEnum.CANCELLED.value,
            total=Decimal("0"),
        )
        # Najm (not registered)
        o5 = _order(  # counter sale, zero VAT → sales_output najm net only
            branch.id,
            najm.id,
            source="cashier",
            status=OrderStatusEnum.DELIVERED.value,
            pos_status="closed",
            total=Decimal("50"),
            total_excl_vat=Decimal("50"),
            vat_amount=Decimal("0"),
            vat_rate=Decimal("0"),
        )
        o6 = _order(  # najm commission → input, non-recoverable
            branch.id,
            najm.id,
            source="aggregator",
            status=OrderStatusEnum.CANCELLED.value,
            total=Decimal("0"),
            aggregator_fee=Decimal("10.50"),
        )
        db.add_all([o1, o2, o3, o4, o5, o6])
        await db.flush()
        db.add(
            OrderDelivery(
                order_id=o4.id, provider="lalamove", cost_total=Decimal("5.25")
            )
        )

        supplier = Supplier(name=f"{MARKER} supplier", is_vat_deductible=True)
        db.add(supplier)
        await db.flush()
        db.add(
            PurchaseOrder(
                reference=f"{MARKER}-PO-{uuid.uuid4().hex[:8]}",
                supplier_id=supplier.id,
                branch_id=branch.id,
                business_date=BDATE,
                status="closed",
                subtotal_net=Decimal("200"),
                vat_total=Decimal("10"),
                total_gross=Decimal("210"),
                total_cost=Decimal("210"),
            )
        )
        await db.commit()
        ids = {"branch_id": branch.id, "fatema_id": fatema.id, "najm_id": najm.id}
    yield ids, Session

    async with Session() as db:
        from sqlalchemy import text

        await db.execute(text("SET session_replication_role = 'replica'"))
        bid = ids["branch_id"]
        order_ids = select(Order.id).where(Order.branch_id == bid)
        await db.execute(
            OrderDelivery.__table__.delete().where(
                OrderDelivery.order_id.in_(order_ids)
            )
        )
        await db.execute(Order.__table__.delete().where(Order.branch_id == bid))
        await db.execute(
            PurchaseOrder.__table__.delete().where(PurchaseOrder.branch_id == bid)
        )
        await db.execute(
            Supplier.__table__.delete().where(Supplier.name.like(f"{MARKER}%"))
        )
        await db.execute(Warehouse.__table__.delete().where(Warehouse.branch_id == bid))
        await db.execute(Branch.__table__.delete().where(Branch.id == bid))
        await db.execute(
            VatLedgerEntry.__table__.delete().where(
                VatLedgerEntry.business_date == BDATE
            )
        )
        await db.commit()


def _by_cat(rows, entity_id):
    return {
        (r["category"], r["direction"]): r
        for r in rows
        if r["legal_entity_id"] == entity_id
    }


async def test_compute_window_splits_every_category(seeded):
    ids, Session = seeded
    async with Session() as db:
        n = await vat_ledger.compute_window(db, BDATE, BDATE)
        await db.commit()
        assert n > 0
        rows = await vat_ledger.read_ledger(db, date_from=BDATE, date_to=BDATE)

    fat = _by_cat(rows, ids["fatema_id"])
    OUT, INP = VatDirectionEnum.OUTPUT.value, VatDirectionEnum.INPUT.value

    sales = fat[(VatCategoryEnum.SALES_OUTPUT.value, OUT)]
    assert sales["net_value"] == Decimal("100.00")
    assert sales["vat_amount"] == Decimal("5.00")
    assert sales["gross_value"] == Decimal("105.00")

    refund = fat[(VatCategoryEnum.SALES_REFUND.value, OUT)]
    assert refund["gross_value"] == Decimal("-21.00")
    assert refund["vat_amount"] == Decimal("-1.00")
    assert refund["net_value"] == Decimal("-20.00")

    comm = fat[(VatCategoryEnum.AGGREGATOR_COMMISSION.value, INP)]
    assert comm["gross_value"] == Decimal("10.50")
    assert comm["net_value"] == Decimal("10.00")
    assert comm["vat_amount"] == Decimal("0.50")
    assert comm["vat_recoverable"] is True

    pay = fat[(VatCategoryEnum.PAYMENT_PROCESSING.value, INP)]
    assert pay["net_value"] == Decimal("2.00")
    assert pay["vat_amount"] == Decimal("0.10")

    courier = fat[(VatCategoryEnum.COURIER_FEES.value, INP)]
    assert courier["gross_value"] == Decimal("5.25")
    assert courier["net_value"] == Decimal("5.00")
    assert courier["vat_amount"] == Decimal("0.25")

    raw = fat[(VatCategoryEnum.RAW_GOODS.value, INP)]
    assert raw["net_value"] == Decimal("200.00")
    assert raw["vat_amount"] == Decimal("10.00")
    assert raw["gross_value"] == Decimal("210.00")

    # Najm: output VAT zero; input commission shown but non-recoverable.
    naj = _by_cat(rows, ids["najm_id"])
    naj_sales = naj[(VatCategoryEnum.SALES_OUTPUT.value, OUT)]
    assert naj_sales["net_value"] == Decimal("50.00")
    assert naj_sales["vat_amount"] == Decimal("0.00")

    naj_comm = naj[(VatCategoryEnum.AGGREGATOR_COMMISSION.value, INP)]
    assert naj_comm["gross_value"] == Decimal("10.50")
    assert naj_comm["net_value"] == Decimal("10.00")
    assert naj_comm["vat_amount"] == Decimal("0.00")
    assert naj_comm["vat_recoverable"] is False


async def test_recompute_is_idempotent(seeded):
    ids, Session = seeded
    async with Session() as db:
        await vat_ledger.compute_window(db, BDATE, BDATE)
        await db.commit()
        first = await db.scalar(
            select(vat_ledger.func.count(VatLedgerEntry.id)).where(
                VatLedgerEntry.business_date == BDATE
            )
        )
    async with Session() as db:
        await vat_ledger.compute_window(db, BDATE, BDATE)
        await db.commit()
        second = await db.scalar(
            select(vat_ledger.func.count(VatLedgerEntry.id)).where(
                VatLedgerEntry.business_date == BDATE
            )
        )
    assert first == second and first > 0
