"""The new-PO form's cost comparison, against a real Postgres.

Which purchase counts as "the latest" is a query decision (DISTINCT ON, a
coalesced date, a status and received-quantity filter, a branch scope), so it
is tested where the query actually runs. Everything happens in one session that
is rolled back.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    PurchaseOrder,
    PurchaseOrderStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.schemas.inventory import (
    PosPurchaseOrderCreate,
    PurchaseLineCostCheckLine,
    PurchaseOrderLineInput,
    SupplierCreate,
)
from app.services.inventory import (
    inventory_service,
    po_cost_check_service,
    supplier_service,
)

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-pocost"


@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def _branch(db, label: str) -> Branch:
    branch = Branch(
        name=f"{MARKER} {label}", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
    )
    db.add(branch)
    await db.flush()
    db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
    db.add(
        BranchInventorySettings(
            branch_id=branch.id,
            inventory_enabled=True,
            go_live_at=utcnow(),
            go_live_sequence=0,
        )
    )
    await db.flush()
    return branch


async def _bought(db, *, branch, user, supplier, item, qty, total, on: date):
    """A received purchase (the till's create-and-receive), dated ``on``."""
    po, _ = await inventory_service.create_pos_purchase_order(
        db,
        branch=branch,
        user=user,
        supplier=supplier,
        warehouse_id=None,
        data=PosPurchaseOrderCreate(
            branch_id=branch.id,
            supplier_id=supplier.id,
            items=[
                PurchaseOrderLineInput(
                    item_id=item.id, quantity=D(qty), entered_total=D(total)
                )
            ],
        ),
    )
    po.invoice_date = on
    await db.flush()
    return po


async def test_latest_received_purchase_and_average_cost(db):
    sharjah = await _branch(db, "sharjah")
    dubai = await _branch(db, "dubai")
    user = User(
        email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
    )
    item = InventoryItem(
        sku=f"{MARKER}-{uuid.uuid4().hex[:8]}",
        name="Butter",
        kind="raw_material",
        tracking_mode="stocked",
        storage_unit="kg",
        ingredient_unit="gram",
        storage_to_ingredient_factor=D("1000"),
    )
    never_bought = InventoryItem(
        sku=f"{MARKER}-{uuid.uuid4().hex[:8]}",
        name="Saffron",
        kind="raw_material",
        tracking_mode="stocked",
        storage_unit="gram",
        ingredient_unit="gram",
        storage_to_ingredient_factor=D("1"),
    )
    db.add_all([user, item, never_bought])
    await db.flush()
    supplier = await supplier_service.create_supplier(
        db, SupplierCreate(name=f"{MARKER} Dairy Co", is_vat_deductible=True)
    )

    # Older at 10.00/kg, newer at 12.00/kg (by invoice date, not creation).
    newer = await _bought(
        db, branch=sharjah, user=user, supplier=supplier, item=item,
        qty="10", total="120", on=date(2026, 10, 1),
    )  # fmt: skip
    await _bought(
        db, branch=sharjah, user=user, supplier=supplier, item=item,
        qty="10", total="100", on=date(2026, 9, 1),
    )  # fmt: skip
    # A voided purchase newer than both, and one in another branch newer still.
    voided = await _bought(
        db, branch=sharjah, user=user, supplier=supplier, item=item,
        qty="1", total="1", on=date(2026, 10, 10),
    )  # fmt: skip
    voided.status = PurchaseOrderStatusEnum.VOIDED.value
    elsewhere = await _bought(
        db, branch=dubai, user=user, supplier=supplier, item=item,
        qty="10", total="150", on=date(2026, 10, 15),
    )  # fmt: skip
    # An approved order that has not arrived yet is not a purchase.
    pending = PurchaseOrder(
        reference=await inventory_service.next_inventory_reference(db, "PO"),
        status=PurchaseOrderStatusEnum.APPROVED.value,
        origin="admin",
        supplier_id=supplier.id,
        branch_id=sharjah.id,
        business_date="2026-10-20",
        invoice_date=date(2026, 10, 20),
    )
    db.add(pending)
    await db.flush()
    await inventory_service.build_po_lines(
        db,
        pending,
        [
            PurchaseOrderLineInput(
                item_id=item.id, quantity=D("1"), entered_total=D("2")
            )
        ],
        is_vat_deductible=True,
    )
    await db.flush()

    lines = [
        PurchaseLineCostCheckLine(
            item_id=item.id, quantity=D("4"), entered_total=D("50")
        ),
        PurchaseLineCostCheckLine(item_id=item.id),  # no figures typed yet
        PurchaseLineCostCheckLine(
            item_id=never_bought.id, quantity=D("1"), entered_total=D("3")
        ),
    ]

    # Every branch: Dubai's 15.00 is the latest.
    unscoped = await po_cost_check_service.check_lines(db, lines, branch_scope=None)
    assert unscoped[0].last_purchase.purchase_order_id == elsewhere.id
    assert unscoped[0].last_purchase.branch_name == f"{MARKER} dubai"

    # Scoped to Sharjah: the voided and the not-yet-received orders are skipped,
    # and invoice date — not creation order — decides "latest".
    scoped = await po_cost_check_service.check_lines(
        db, lines, branch_scope=select(Branch.id).where(Branch.id == sharjah.id)
    )
    first, untyped, unknown = scoped
    assert first.unit_cost == D("12.5")  # 50 over 4, gross: VAT stays in cost
    last = first.last_purchase
    assert last.purchase_order_id == newer.id
    assert last.reference == newer.reference
    assert last.supplier_name == f"{MARKER} Dairy Co"
    assert last.purchased_on == date(2026, 10, 1)
    assert last.unit_cost == D("12")
    assert first.vs_last_purchase.verdict == "worse"
    assert first.vs_last_purchase.change == D("0.0417")

    # Average over every surviving layer, every branch: 10×12 + 10×10 + 1×1 +
    # 10×15 = 371 over 31 kg. (The voided row was only re-labelled here, so its
    # layer is still on hand — the void flow is what reverses stock.)
    assert first.average_cost == D("11.9677419355")
    assert first.vs_average_cost.verdict == "worse"

    # A line with no figures still carries the references, just no verdict.
    assert untyped.unit_cost is None
    assert untyped.last_purchase.purchase_order_id == newer.id
    assert untyped.average_cost == first.average_cost
    assert untyped.vs_last_purchase is None and untyped.vs_average_cost is None

    # Never bought and nothing on hand: unknown, not zero.
    assert unknown.unit_cost == D("3")
    assert unknown.last_purchase is None and unknown.vs_last_purchase is None
    assert unknown.average_cost is None and unknown.vs_average_cost is None
