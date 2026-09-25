"""
The shift report knows which of its items a purchase order already covers.

2026-09-25, Sharjah: 25 kg of butter was typed into the end-of-shift report's
**Received** column — which posts a purchase of its own — and the same delivery
was received on PO-004481 an hour later. Stock counted it twice. Each report line
now carries the PO side's view of its item (`source_summary.purchase_orders`), so
the register can warn the moment someone types a Received figure for it:

* ``received`` — PO receipts of the item at the branch on the report's business
  date (a receipt reversed since, or on a voided PO, does not count);
* ``expected`` — open POs for the item at the branch due by that date, with what
  is still to arrive.

Runs against a real Postgres and rolls everything back.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatusEnum,
    Supplier,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    ShiftInventoryReport,
    ShiftInventoryReportLine,
    ShiftInventoryReportStatusEnum,
)
from app.models.user import User
from app.services.inventory import inventory_service, report_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-report-po"
BUSINESS_DATE = "2026-09-25"


@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def _world(db):
    branch = Branch(
        name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:10]}"
    )
    db.add(branch)
    await db.flush()
    warehouse = Warehouse(branch_id=branch.id, name="Default stock", is_default=True)
    db.add(warehouse)
    db.add(
        BranchInventorySettings(
            branch_id=branch.id,
            inventory_enabled=True,
            go_live_at=report_service.utcnow(),
            go_live_sequence=0,
        )
    )
    user = User(
        email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
    )
    supplier = Supplier(name=f"{MARKER} supplier")
    db.add_all([user, supplier])
    items = []
    for name in ("Butter", "Cream Cheese", "Flour"):
        item = InventoryItem(
            sku=f"{MARKER}-{uuid.uuid4().hex[:10]}",
            name=name,
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="g",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1"),
        )
        db.add(item)
        items.append(item)
    await db.flush()
    return branch, warehouse, user, supplier, items


def _po(
    branch, supplier, user, *, status, delivery_date=None, business_date=BUSINESS_DATE
):
    return PurchaseOrder(
        reference=f"PO-{MARKER}-{uuid.uuid4().hex[:8]}",
        status=status,
        origin="admin",
        supplier_id=supplier.id,
        branch_id=branch.id,
        business_date=business_date,
        delivery_date=delivery_date,
        creator_id=user.id,
    )


async def _receive(
    db, *, po, branch, warehouse, item, user, qty, business_date=BUSINESS_DATE
):
    txn = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.PURCHASING.value
        ),
        type=InventoryTransactionTypeEnum.PURCHASING.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        warehouse_id=warehouse.id,
        business_date=business_date,
        creator_id=user.id,
        purchase_order_id=po.id,
        idempotency_key=f"{MARKER}:{uuid.uuid4()}",
        items=[
            InventoryTransactionItem(
                item_id=item.id,
                quantity=Decimal(str(qty)),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=Decimal("0.05"),
            )
        ],
    )
    db.add(txn)
    await db.flush()
    return await inventory_service.post_transaction(db, transaction=txn, user=user)


def _report(branch):
    return ShiftInventoryReport(
        branch_id=branch.id,
        business_date=BUSINESS_DATE,
        status=ShiftInventoryReportStatusEnum.DRAFT.value,
        idempotency_key=f"{MARKER}:{uuid.uuid4()}",
        template_snapshot={"report_type": "raw_materials"},
    )


async def test_a_line_knows_its_same_day_receipt_and_its_open_po(db):
    branch, warehouse, user, supplier, (butter, cheese, flour) = await _world(db)

    received_po = _po(
        branch, supplier, user, status=PurchaseOrderStatusEnum.CLOSED.value
    )
    open_po = _po(
        branch,
        supplier,
        user,
        status=PurchaseOrderStatusEnum.APPROVED.value,
        delivery_date=date(2026, 9, 25),
    )
    later_po = _po(
        branch,
        supplier,
        user,
        status=PurchaseOrderStatusEnum.APPROVED.value,
        delivery_date=date(2026, 9, 30),
    )
    db.add_all([received_po, open_po, later_po])
    await db.flush()
    db.add_all(
        [
            PurchaseOrderItem(
                purchase_order_id=received_po.id,
                item_id=butter.id,
                quantity=25000,
                received_quantity=25000,
            ),
            PurchaseOrderItem(
                purchase_order_id=open_po.id,
                item_id=cheese.id,
                quantity=4500,
                received_quantity=0,
            ),
            PurchaseOrderItem(
                purchase_order_id=later_po.id,
                item_id=flour.id,
                quantity=10000,
                received_quantity=0,
            ),
        ]
    )
    await db.flush()
    await _receive(
        db,
        po=received_po,
        branch=branch,
        warehouse=warehouse,
        item=butter,
        user=user,
        qty=25000,
    )

    report = _report(branch)
    overlap = await report_service._purchase_order_overlap(
        db, report, [butter.id, cheese.id, flour.id]
    )

    assert overlap[butter.id]["received"] == [
        {"reference": received_po.reference, "quantity": "25000.0000"}
    ]
    assert overlap[cheese.id]["expected"] == [
        {
            "reference": open_po.reference,
            "quantity": "4500.0000",
            "delivery_date": "2026-09-25",
        }
    ]
    assert flour.id not in overlap, "a PO due next week is not today's delivery"


async def test_a_reversed_receipt_or_another_days_receipt_does_not_count(db):
    branch, warehouse, user, supplier, (butter, _, _) = await _world(db)
    yesterday_po = _po(
        branch,
        supplier,
        user,
        status=PurchaseOrderStatusEnum.CLOSED.value,
        business_date="2026-09-24",
    )
    reversed_po = _po(
        branch, supplier, user, status=PurchaseOrderStatusEnum.CLOSED.value
    )
    db.add_all([yesterday_po, reversed_po])
    await db.flush()
    await _receive(
        db,
        po=yesterday_po,
        branch=branch,
        warehouse=warehouse,
        item=butter,
        user=user,
        qty=1000,
        business_date="2026-09-24",
    )
    receipt = await _receive(
        db,
        po=reversed_po,
        branch=branch,
        warehouse=warehouse,
        item=butter,
        user=user,
        qty=2000,
    )
    from app.services.inventory import ledger_service

    await ledger_service.reverse_transaction(
        db, transaction_id=receipt.id, user=user, reason="test"
    )

    overlap = await report_service._purchase_order_overlap(
        db, _report(branch), [butter.id]
    )
    assert overlap == {}


async def test_the_overlap_is_stamped_on_lines_and_cleared_when_it_goes(db):
    branch, _, _, _, (butter, cheese, _) = await _world(db)
    butter_line = ShiftInventoryReportLine(
        item_id=butter.id, unit="g", source_summary={"item_name": "Butter"}
    )
    cheese_line = ShiftInventoryReportLine(
        item_id=cheese.id,
        unit="g",
        source_summary={
            "item_name": "Cream Cheese",
            "purchase_orders": {"stale": True},
        },
    )
    found = {
        "received": [{"reference": "PO-1", "quantity": "25000.0000"}],
        "expected": [],
    }

    report_service._apply_purchase_order_overlap(
        [butter_line, cheese_line], {butter.id: found}
    )

    assert butter_line.source_summary["purchase_orders"] == found
    assert butter_line.source_summary["item_name"] == "Butter"
    assert "purchase_orders" not in cheese_line.source_summary
