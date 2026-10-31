"""PO stock not yet in the P&L, end to end against a real Postgres.

One world, worked by hand. Branch A buys 10 kg of flour on PO-1 (AED 210 with
AED 10 of VAT, so 20/kg net) and 5 kg of sugar on PO-2 (52.50 with 2.50 VAT, so
10/kg net). Then, through the real poster and so the real costing engine:

  flour  5 kg ─▶ batch of 10 cakes (0.5 kg each)
                   ├─ 4 cakes transferred to branch B ─▶ order O1 sells 2   1.0 kg
                   ├─ order O2 sells 3 at A                                 1.5 kg
                   │    └─ and returns 1                                   −0.5 kg
                   └─ order O3 (cancelled, uncharged: not in any P&L)       —
         0.5 kg ─▶ order O4 sells it as flour                               0.5 kg
         1 kg   ─▶ internal use (a write-off: no P&L)                       —
  sugar  2 kg sold by O5 *before* PO-2 was keyed in (negative stock),
         which PO-2's receipt then pays back                                2.0 kg
  butter a count finds 8 kg nobody priced; PO-3 brings 2 kg (20 net) and
         prices them; O6 sells 8 kg — FIFO, the found 8                     —

Flour consumed: 1 + 1.5 − 0.5 + 0.5 = 2.5 kg of 10 → 25% of 200 = 50.00.
Sugar consumed: 2 of 5 → 40% of 50 = 20.00.
Butter consumed: none. PO-3 *priced* the found stock, so the engine books O6 at
its price, but its own 2 kg are still on the shelf. Following the price would
call PO-3 100% consumed and drop 20 of shelf stock out of Net.

Net splits in two. Flour used elsewhere: O3's cake (0.5 kg, an order no P&L
counts) and the internal use (1 kg) → 15% = 30.00. The rest is on the shelf:
3.5 kg of flour (70) and 5 cakes — 3 at A after the return, 2 at B — holding
2.5 kg (50): 120.00, which is Net less the 30.
PO-1 also carries a misc line (rent), which is on the P&L already and not here.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderMiscCategory,
    PurchaseOrderMiscItem,
    Supplier,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory import InventoryTransactionTypeEnum as T
from app.models.inventory_v2 import BranchInventorySettings
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.services.inventory import inventory_service
from app.services.orders import pnl_purchase_orders

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

D = Decimal
#: A shop day nothing else in the test database trades on.
DAY = "2031-02-11"
AT = datetime(2031, 2, 11, 8, 0, tzinfo=UTC)


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


async def _branch(db, tag, name):
    branch = Branch(name=f"po-stock {name} {tag}", reference=f"pos-{name}-{tag}")
    db.add(branch)
    await db.flush()
    warehouse = Warehouse(branch_id=branch.id, name="Default", is_default=True)
    db.add(warehouse)
    db.add(
        BranchInventorySettings(
            branch_id=branch.id,
            inventory_enabled=True,
            production_enabled=True,
            sales_consumption_enabled=True,
            allow_negative_stock=True,
            go_live_at=inventory_service.utcnow(),
            go_live_sequence=0,
        )
    )
    await db.flush()
    return branch, warehouse


async def _post(db, kind, branch, warehouse, lines, **extra):
    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(db, kind.value),
        type=kind.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        warehouse_id=warehouse.id,
        business_date=DAY,
        items=[
            InventoryTransactionItem(
                item_id=item.id,
                quantity=D(quantity),
                unit="storage",
                conversion_factor=D("1"),
                unit_cost=D(unit_cost),
            )
            for item, quantity, unit_cost in lines
        ],
        **extra,
    )
    db.add(transaction)
    await db.flush()
    await inventory_service.post_transaction(db, transaction=transaction, user=None)


def _item(tag, name, kind, unit):
    return InventoryItem(
        sku=f"pos-{name}-{tag}",
        name=name,
        kind=kind,
        tracking_mode="stocked",
        storage_unit=unit,
        ingredient_unit=unit,
        storage_to_ingredient_factor=D("1"),
    )


async def test_po_stock_consumed_is_traced_to_the_pnl_orders(engine):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    async with Session() as db:
        a, wa = await _branch(db, tag, "a")
        b, wb = await _branch(db, tag, "b")
        flour = _item(tag, "Flour", "raw_material", "kg")
        sugar = _item(tag, "Sugar", "raw_material", "kg")
        butter = _item(tag, "Butter", "raw_material", "kg")
        cake = _item(tag, "Cake", "produced_good", "piece")
        supplier = Supplier(name=f"po-stock {tag}", allows_misc_items=True)
        category = PurchaseOrderMiscCategory(name=f"po-stock {tag}")
        db.add_all([flour, sugar, butter, cake, supplier, category])
        await db.flush()

        def order(suffix, status=OrderStatusEnum.DELIVERED, branch=a):
            return Order(
                order_number=f"POS-{tag}-{suffix}",
                email="po-stock@example.com",
                branch_id=branch.id,
                source="aggregator",
                status=status,
                delivery_method=DeliveryMethodEnum.DELIVERY,
                subtotal=D("0"),
                total=D("0"),
                vat_rate=D("0.05"),
                vat_amount=D("0"),
                total_excl_vat=D("0"),
                created_at=AT,
            )

        orders = {
            "O1": order("O1", branch=b),
            "O2": order("O2"),
            "O3": order("O3", status=OrderStatusEnum.CANCELLED),
            "O4": order("O4"),
            "O5": order("O5"),
            "O6": order("O6"),
        }
        db.add_all(orders.values())
        await db.flush()

        def po(ref, lines):
            purchase_order = PurchaseOrder(
                reference=f"PO-{ref}-{tag}",
                status="closed",
                supplier_id=supplier.id,
                branch_id=a.id,
                business_date=DAY,
            )
            db.add(purchase_order)
            for item, qty, gross, vat in lines:
                purchase_order.items.append(
                    PurchaseOrderItem(
                        item_id=item.id,
                        quantity=D(qty),
                        received_quantity=D(qty),
                        entered_total=D(gross),
                        vat_amount=D(vat),
                        net_total=D(gross) - D(vat),
                        unit_cost=D(gross) / D(qty),
                        total_cost=D(gross),
                    )
                )
            return purchase_order

        po1 = po("1", [(flour, "10", "210", "10")])
        await db.flush()
        db.add(
            PurchaseOrderMiscItem(
                purchase_order_id=po1.id,
                name="Rent",
                quantity=D("1"),
                storage_unit="month",
                entered_total=D("1050.00"),
                vat_amount=D("50.00"),
                net_total=D("1000.00"),
                unit_cost=D("1050.00"),
                category_id=category.id,
                period_from=date.fromisoformat(DAY),
                period_to=date.fromisoformat(DAY),
            )
        )
        await _post(
            db, T.PURCHASING, a, wa, [(flour, "10", "21")], purchase_order_id=po1.id
        )

        # The batch: inputs first, then the output, sharing one group.
        batch = uuid.uuid4()
        await _post(
            db,
            T.CONSUMPTION_FROM_PRODUCTION,
            a,
            wa,
            [(flour, "5", "0")],
            correction_group_id=batch,
        )
        await _post(
            db, T.PRODUCTION, a, wa, [(cake, "10", "0")], correction_group_id=batch
        )
        transfer = str(uuid.uuid4())
        await _post(
            db,
            T.TRANSFER_SEND,
            a,
            wa,
            [(cake, "4", "0")],
            source_type="transfer",
            source_id=transfer,
        )
        await _post(
            db,
            T.TRANSFER_RECEIVE,
            b,
            wb,
            [(cake, "4", "0")],
            source_type="transfer",
            source_id=transfer,
        )
        await _post(
            db,
            T.CONSUMPTION_FROM_ORDERS,
            b,
            wb,
            [(cake, "2", "0")],
            order_id=orders["O1"].id,
        )
        for key, item, qty in (
            ("O2", cake, "3"),
            ("O3", cake, "1"),
            ("O4", flour, "0.5"),
        ):
            await _post(
                db,
                T.CONSUMPTION_FROM_ORDERS,
                a,
                wa,
                [(item, qty, "0")],
                order_id=orders[key].id,
            )
        await _post(
            db,
            T.RETURN_FROM_ORDERS,
            a,
            wa,
            [(cake, "1", "0")],
            order_id=orders["O2"].id,
            source_type="order_return",
        )
        await _post(db, T.INTERNAL_USE, a, wa, [(flour, "1", "0")])

        # Sugar sold before its PO was keyed in: the stock goes negative, and
        # the receipt that lands afterwards is what prices those 2 kg.
        await _post(
            db,
            T.CONSUMPTION_FROM_ORDERS,
            a,
            wa,
            [(sugar, "2", "0")],
            order_id=orders["O5"].id,
        )
        po2 = po("2", [(sugar, "5", "52.50", "2.50")])
        await db.flush()
        await _post(
            db, T.PURCHASING, a, wa, [(sugar, "5", "10.5")], purchase_order_id=po2.id
        )

        await _post(db, T.INVENTORY_COUNT, a, wa, [(butter, "8", "0")])
        po3 = po("3", [(butter, "2", "21", "1")])
        await db.flush()
        await _post(
            db, T.PURCHASING, a, wa, [(butter, "2", "10.5")], purchase_order_id=po3.id
        )
        await _post(
            db,
            T.CONSUMPTION_FROM_ORDERS,
            a,
            wa,
            [(butter, "8", "0")],
            order_id=orders["O6"].id,
        )

        report = await pnl_purchase_orders.build(
            db, date_from=DAY, date_to=DAY, branch_ids=[a.id]
        )
        rows = {r.reference: r for r in report.rows}
        assert set(rows) == {po1.reference, po2.reference, po3.reference}
        one, two, three = rows[po1.reference], rows[po2.reference], rows[po3.reference]

        # Stock lines only, net of VAT: the 1000 of rent is not here.
        # (received, consumed, net, of which used elsewhere, of which in stock)
        def figures(r):
            return (r.received, r.consumed, r.net, r.used_elsewhere, r.in_stock)

        assert figures(one) == tuple(D(x) for x in ("200", "50", "150", "30", "120"))
        assert figures(two) == tuple(D(x) for x in ("50", "20", "30", "0", "30"))
        assert figures(three) == tuple(D(x) for x in ("20", "0", "20", "0", "20"))
        assert figures(report) == tuple(D(x) for x in ("270", "70", "200", "30", "170"))

        # Consumed is to date and wherever the stock went: B's sale counts, but
        # B bought nothing, so B's own slice of POs is empty.
        assert (
            await pnl_purchase_orders.build(
                db, date_from=DAY, date_to=DAY, branch_ids=[b.id]
            )
        ).rows == []
        # Outside the window, nothing.
        assert (
            await pnl_purchase_orders.build(
                db, date_from="2031-02-12", date_to="2031-02-12", branch_ids=[a.id]
            )
        ).rows == []
        await db.rollback()
