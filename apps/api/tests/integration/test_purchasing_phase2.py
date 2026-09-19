"""Suppliers, VAT-split purchase orders and the till's create-and-receive.

Against a real Postgres: a supplier with contacts, the purchased-item mapping
rule, the VAT split on a deductible supplier, an admin PO received into FIFO
stock, and a POS create-and-receive that lands cost in one call.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.exceptions import BadRequestError
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCostLayer,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatusEnum,
    Supplier,
    SupplierContact,
    SupplierItem,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.user import User
from app.schemas.inventory import (
    PosPurchaseOrderCreate,
    PurchaseOrderLineInput,
    SupplierContactInput,
    SupplierCreate,
    SupplierItemUpsert,
)
from app.services.inventory import inventory_service, supplier_service

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-po2"
D = Decimal


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def env(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        branch = Branch(
            name=f"{MARKER} branch", reference=f"{MARKER}-{uuid.uuid4().hex[:12]}"
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
        user = User(
            email=f"{MARKER}-{uuid.uuid4().hex[:8]}@example.com", hashed_password="x"
        )
        db.add(user)
        raw = InventoryItem(
            sku=f"{MARKER}-raw-{uuid.uuid4().hex[:8]}",
            name="Butter",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="gram",
            ingredient_unit="gram",
            storage_to_ingredient_factor=D("1"),
        )
        produced = InventoryItem(
            sku=f"{MARKER}-prod-{uuid.uuid4().hex[:8]}",
            name="Brownie",
            kind="produced_good",
            tracking_mode="stocked",
            storage_unit="unit",
            ingredient_unit="unit",
            storage_to_ingredient_factor=D("1"),
        )
        db.add_all([raw, produced])
        await db.commit()
        ids = (branch.id, user.id, raw.id, produced.id)

    yield ids

    branch_id, user_id, raw_id, produced_id = ids
    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        line_ids = select(InventoryTransactionItem.id).where(
            InventoryTransactionItem.transaction_id.in_(
                select(InventoryTransaction.id).where(
                    InventoryTransaction.branch_id == branch_id
                )
            )
        )
        from app.models.inventory import InventoryCostLayerConsumption

        await db.execute(
            InventoryCostLayerConsumption.__table__.delete().where(
                InventoryCostLayerConsumption.consuming_line_id.in_(line_ids)
            )
        )
        await db.execute(
            InventoryCostLayer.__table__.delete().where(
                InventoryCostLayer.branch_id == branch_id
            )
        )
        await db.execute(
            PurchaseOrderItem.__table__.delete().where(
                PurchaseOrderItem.purchase_order_id.in_(
                    select(PurchaseOrder.id).where(PurchaseOrder.branch_id == branch_id)
                )
            )
        )
        await db.execute(
            PurchaseOrder.__table__.delete().where(PurchaseOrder.branch_id == branch_id)
        )
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(
                    select(InventoryTransaction.id).where(
                        InventoryTransaction.branch_id == branch_id
                    )
                )
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == branch_id
            )
        )
        await db.execute(
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id.in_([raw_id, produced_id])
            )
        )
        await db.execute(
            SupplierItem.__table__.delete().where(
                SupplierItem.item_id.in_([raw_id, produced_id])
            )
        )
        supplier_ids = select(Supplier.id).where(Supplier.name.like(f"{MARKER}%"))
        await db.execute(
            SupplierContact.__table__.delete().where(
                SupplierContact.supplier_id.in_(supplier_ids)
            )
        )
        await db.execute(
            SupplierItem.__table__.delete().where(
                SupplierItem.supplier_id.in_(supplier_ids)
            )
        )
        await db.execute(
            Supplier.__table__.delete().where(Supplier.name.like(f"{MARKER}%"))
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id == branch_id
            )
        )
        await db.execute(
            InventoryItem.__table__.delete().where(
                InventoryItem.id.in_([raw_id, produced_id])
            )
        )
        await db.execute(
            Warehouse.__table__.delete().where(Warehouse.branch_id == branch_id)
        )
        await db.execute(User.__table__.delete().where(User.id == user_id))
        await db.execute(Branch.__table__.delete().where(Branch.id == branch_id))
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


async def _layer_total(db, item_id):
    return D(
        str(
            (
                await db.execute(
                    select(
                        func.coalesce(
                            func.sum(InventoryCostLayer.remaining_quantity), 0
                        )
                    ).where(InventoryCostLayer.item_id == item_id)
                )
            ).scalar()
        )
    )


async def test_supplier_with_contacts_and_the_purchased_item_rule(engine, env):
    branch_id, user_id, raw_id, produced_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        supplier = await supplier_service.create_supplier(
            db,
            SupplierCreate(
                name=f"{MARKER} Dairy",
                is_vat_deductible=True,
                contacts=[
                    SupplierContactInput(name="Sam", phone="+971500000000"),
                    SupplierContactInput(name="Ana", email="ana@dairy.example"),
                ],
            ),
        )
        assert len(supplier.contacts) == 2

        # A raw material maps fine.
        await supplier_service.set_supplier_items(
            db,
            supplier.id,
            [SupplierItemUpsert(item_id=raw_id)],
        )
        # A produced good cannot be supplied by a vendor.
        with pytest.raises(BadRequestError, match="produced"):
            await supplier_service.set_supplier_items(
                db,
                supplier.id,
                [SupplierItemUpsert(item_id=produced_id)],
            )
        await db.rollback()


async def test_contact_requires_email_or_phone():
    with pytest.raises(ValueError, match="email or a phone"):
        SupplierContactInput(name="Nameless")


async def test_admin_po_splits_vat_and_receives_into_fifo_stock(engine, env):
    branch_id, user_id, raw_id, produced_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        supplier = await supplier_service.create_supplier(
            db, SupplierCreate(name=f"{MARKER} VATco", is_vat_deductible=True)
        )
        po = PurchaseOrder(
            reference=await inventory_service.next_inventory_reference(db, "PO"),
            status=PurchaseOrderStatusEnum.DRAFT.value,
            origin="admin",
            supplier_id=supplier.id,
            branch_id=branch_id,
            business_date="2026-09-18",
            creator_id=user_id,
        )
        db.add(po)
        await db.flush()
        await inventory_service.build_po_lines(
            db,
            po,
            [
                PurchaseOrderLineInput(
                    item_id=raw_id, quantity=D("10"), entered_total=D("105")
                )
            ],
            is_vat_deductible=True,
        )
        # 105 gross over 10 units: unit 10.5, VAT 5, net 100.
        assert po.subtotal_net == D("100.00")
        assert po.vat_total == D("5.00")
        assert po.total_gross == D("105.00")
        line = (
            await db.execute(
                select(PurchaseOrderItem).where(
                    PurchaseOrderItem.purchase_order_id == po.id
                )
            )
        ).scalar_one()
        assert line.unit_cost == D("10.500000")
        assert line.vat_amount == D("5.00")

        # Approve, then receive in full → posts a FIFO layer at the gross cost.
        po.status = PurchaseOrderStatusEnum.APPROVED.value
        await db.flush()
        received = {line.id: line.quantity}
        txn = await inventory_service.receive_purchase_order(
            db, purchase_order=po, user=user, received=received
        )
        assert D(str(txn.paid_tax)) == D("5.00")
        level = await inventory_service.level_for(
            db, raw_id, (await inventory_service.default_warehouse(db, branch_id)).id
        )
        assert level.quantity == D("10.0000")
        assert level.average_cost == D("10.500000")
        assert await _layer_total(db, raw_id) == D("10.000000")
        assert po.status == PurchaseOrderStatusEnum.CLOSED.value
        await db.rollback()


async def test_short_receipt_records_variance_and_closes(engine, env):
    """A short delivery closes the PO one-shot: the received quantity and the
    variance reason are recorded, and only what arrived posts to stock."""
    branch_id, user_id, raw_id, produced_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        supplier = await supplier_service.create_supplier(
            db, SupplierCreate(name=f"{MARKER} Short", is_vat_deductible=False)
        )
        po = PurchaseOrder(
            reference=await inventory_service.next_inventory_reference(db, "PO"),
            status=PurchaseOrderStatusEnum.APPROVED.value,
            origin="admin",
            supplier_id=supplier.id,
            branch_id=branch_id,
            business_date="2026-09-18",
            creator_id=user_id,
        )
        db.add(po)
        await db.flush()
        await inventory_service.build_po_lines(
            db,
            po,
            [
                PurchaseOrderLineInput(
                    item_id=raw_id, quantity=D("10"), entered_total=D("100")
                )
            ],
            is_vat_deductible=False,
        )
        line = (
            await db.execute(
                select(PurchaseOrderItem).where(
                    PurchaseOrderItem.purchase_order_id == po.id
                )
            )
        ).scalar_one()

        # A short line with no reason is refused.
        with pytest.raises(BadRequestError):
            await inventory_service.receive_purchase_order(
                db, purchase_order=po, user=user, received={line.id: D("6")}
            )
        await db.rollback()


async def test_short_receipt_with_reason_closes_and_over_receipt_is_allowed(
    engine, env
):
    branch_id, user_id, raw_id, produced_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    # Short-with-reason closes and records the variance.
    async with Session() as db:
        user = await db.get(User, user_id)
        supplier = await supplier_service.create_supplier(
            db, SupplierCreate(name=f"{MARKER} ShortOK", is_vat_deductible=False)
        )
        po = PurchaseOrder(
            reference=await inventory_service.next_inventory_reference(db, "PO"),
            status=PurchaseOrderStatusEnum.APPROVED.value,
            origin="admin",
            supplier_id=supplier.id,
            branch_id=branch_id,
            business_date="2026-09-18",
            creator_id=user_id,
        )
        db.add(po)
        await db.flush()
        await inventory_service.build_po_lines(
            db,
            po,
            [
                PurchaseOrderLineInput(
                    item_id=raw_id, quantity=D("10"), entered_total=D("100")
                )
            ],
            is_vat_deductible=False,
        )
        line = (
            await db.execute(
                select(PurchaseOrderItem).where(
                    PurchaseOrderItem.purchase_order_id == po.id
                )
            )
        ).scalar_one()
        await inventory_service.receive_purchase_order(
            db,
            purchase_order=po,
            user=user,
            received={line.id: D("6")},
            reasons={line.id: "two cases missing"},
        )
        assert po.status == PurchaseOrderStatusEnum.CLOSED.value
        assert line.received_quantity == D("6.0000")
        assert line.variance_reason == "two cases missing"
        level = await inventory_service.level_for(
            db, raw_id, (await inventory_service.default_warehouse(db, branch_id)).id
        )
        # Only the 6 that arrived posted to stock.
        assert level.quantity == D("6.0000")
        await db.rollback()

    # An over-receipt (more than ordered) is allowed with a reason.
    async with Session() as db:
        user = await db.get(User, user_id)
        supplier = await supplier_service.create_supplier(
            db, SupplierCreate(name=f"{MARKER} Over", is_vat_deductible=False)
        )
        po = PurchaseOrder(
            reference=await inventory_service.next_inventory_reference(db, "PO"),
            status=PurchaseOrderStatusEnum.APPROVED.value,
            origin="admin",
            supplier_id=supplier.id,
            branch_id=branch_id,
            business_date="2026-09-18",
            creator_id=user_id,
        )
        db.add(po)
        await db.flush()
        await inventory_service.build_po_lines(
            db,
            po,
            [
                PurchaseOrderLineInput(
                    item_id=raw_id, quantity=D("10"), entered_total=D("100")
                )
            ],
            is_vat_deductible=False,
        )
        line = (
            await db.execute(
                select(PurchaseOrderItem).where(
                    PurchaseOrderItem.purchase_order_id == po.id
                )
            )
        ).scalar_one()
        await inventory_service.receive_purchase_order(
            db,
            purchase_order=po,
            user=user,
            received={line.id: D("12")},
            reasons={line.id: "supplier sent two extra"},
        )
        assert po.status == PurchaseOrderStatusEnum.CLOSED.value
        assert line.received_quantity == D("12.0000")
        level = await inventory_service.level_for(
            db, raw_id, (await inventory_service.default_warehouse(db, branch_id)).id
        )
        assert level.quantity == D("12.0000")
        await db.rollback()


async def test_over_receipt_does_not_reclaim_more_vat_than_the_invoice(engine, env):
    """The invoice's VAT is fixed at the ordered quantity; receiving more than was
    ordered must not pro-rata the recoverable VAT above the invoiced amount."""
    branch_id, user_id, raw_id, produced_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        supplier = await supplier_service.create_supplier(
            db, SupplierCreate(name=f"{MARKER} OverVAT", is_vat_deductible=True)
        )
        po = PurchaseOrder(
            reference=await inventory_service.next_inventory_reference(db, "PO"),
            status=PurchaseOrderStatusEnum.APPROVED.value,
            origin="admin",
            supplier_id=supplier.id,
            branch_id=branch_id,
            business_date="2026-09-18",
            creator_id=user_id,
        )
        db.add(po)
        await db.flush()
        # 105 gross over 10 units: VAT 5.00.
        await inventory_service.build_po_lines(
            db,
            po,
            [
                PurchaseOrderLineInput(
                    item_id=raw_id, quantity=D("10"), entered_total=D("105")
                )
            ],
            is_vat_deductible=True,
        )
        line = (
            await db.execute(
                select(PurchaseOrderItem).where(
                    PurchaseOrderItem.purchase_order_id == po.id
                )
            )
        ).scalar_one()
        txn = await inventory_service.receive_purchase_order(
            db,
            purchase_order=po,
            user=user,
            received={line.id: D("12")},  # two more than ordered
            reasons={line.id: "supplier sent two extra"},
        )
        # Capped at the invoiced VAT, not 12/10 * 5 = 6.00.
        assert D(str(txn.paid_tax)) == D("5.00")
        await db.rollback()


async def test_pos_create_and_receive_lands_cost_in_one_call(engine, env):
    branch_id, user_id, raw_id, produced_id = env
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        user = await db.get(User, user_id)
        branch = await db.get(Branch, branch_id)
        supplier = await supplier_service.create_supplier(
            db, SupplierCreate(name=f"{MARKER} Cash", is_vat_deductible=False)
        )
        po, txn = await inventory_service.create_pos_purchase_order(
            db,
            branch=branch,
            user=user,
            supplier=supplier,
            warehouse_id=None,
            data=PosPurchaseOrderCreate(
                branch_id=branch_id,
                supplier_id=supplier.id,
                items=[
                    PurchaseOrderLineInput(
                        item_id=raw_id, quantity=D("20"), entered_total=D("200")
                    )
                ],
            ),
        )
        assert po.origin == "pos"
        assert po.status == PurchaseOrderStatusEnum.CLOSED.value
        assert po.vat_total == D("0.00")  # non-deductible supplier
        level = await inventory_service.level_for(
            db, raw_id, (await inventory_service.default_warehouse(db, branch_id)).id
        )
        assert level.quantity == D("20.0000")
        assert level.average_cost == D("10.000000")
        await db.rollback()
