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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.aggregator import AggregatorOrder
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLineCost,
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
from app.services.orders.order_pnl import in_pnl_clause

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


async def test_which_orders_count_is_asked_once_and_matches_the_pnl(engine):
    """Whether the P&L counts an order is asked once per order per trace, over
    just the orders the draws name — and answers exactly as asking each draw
    against every P&L order did.

    PO-1 brings 10 kg of flour (200 net). Before it is keyed in, N (cancelled,
    uncharged) sells 1 kg on credit, which the receipt pays back. Then:

      P  delivered, sells 2 kg, then 4 more                     drawn      6
      F  cancelled, but the marketplace's statement bills him   drawn      1
      C  cancelled, uncharged                                   elsewhere  1
      R  cancelled, uncharged, sells 2 kg and returns 1         elsewhere  1
      N  cancelled, uncharged, on credit                        elsewhere  1

    P's second sale takes the last 3 kg of the PO's layer and then the 1 kg R
    returned — content that reaches P a round later, through R's sale, so P is
    met in two rounds and asked about once. 7 of 10 kg consumed → 140.00; 3
    used elsewhere → 60.00; nothing on the shelf.
    """
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    async with Session() as db:
        a, wa = await _branch(db, tag, "a")
        flour = _item(tag, "Flour", "raw_material", "kg")
        supplier = Supplier(name=f"po-stock {tag}")
        db.add_all([flour, supplier])
        await db.flush()

        def order(suffix, status):
            return Order(
                order_number=f"POS-{tag}-{suffix}",
                email="po-stock@example.com",
                branch_id=a.id,
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

        cancelled = OrderStatusEnum.CANCELLED
        orders = {
            "P": order("P", OrderStatusEnum.DELIVERED),
            "F": order("F", cancelled),
            "C": order("C", cancelled),
            "R": order("R", cancelled),
            "N": order("N", cancelled),
        }
        db.add_all(orders.values())
        await db.flush()
        # F's charge is the marketplace statement's: the correlated EXISTS
        # against aggregator_order that made the old per-draw subquery slow.
        db.add(
            AggregatorOrder(
                channel="noon",
                external_order_id=f"po-stock-{tag}",
                business_date=DAY,
                status="cancelled",
                net_payable=D("-12.00"),
                mm_order_id=orders["F"].id,
            )
        )

        async def sell(key, qty):
            await _post(
                db,
                T.CONSUMPTION_FROM_ORDERS,
                a,
                wa,
                [(flour, qty, "0")],
                order_id=orders[key].id,
            )

        await sell("N", "1")
        purchase_order = PurchaseOrder(
            reference=f"PO-1-{tag}",
            status="closed",
            supplier_id=supplier.id,
            branch_id=a.id,
            business_date=DAY,
        )
        purchase_order.items.append(
            PurchaseOrderItem(
                item_id=flour.id,
                quantity=D("10"),
                received_quantity=D("10"),
                entered_total=D("210"),
                vat_amount=D("10"),
                net_total=D("200"),
                unit_cost=D("21"),
                total_cost=D("210"),
            )
        )
        db.add(purchase_order)
        await db.flush()
        await _post(
            db,
            T.PURCHASING,
            a,
            wa,
            [(flour, "10", "21")],
            purchase_order_id=purchase_order.id,
        )
        for key, qty in (("P", "2"), ("F", "1"), ("C", "1"), ("R", "2")):
            await sell(key, qty)
        await _post(
            db,
            T.RETURN_FROM_ORDERS,
            a,
            wa,
            [(flour, "1", "0")],
            order_id=orders["R"].id,
            source_type="order_return",
        )
        await sell("P", "4")

        report = await pnl_purchase_orders.build(
            db, date_from=DAY, date_to=DAY, branch_ids=[a.id]
        )
        (row,) = report.rows
        assert (row.received, row.consumed, row.net) == (D("200"), D("140"), D("60"))
        assert (row.used_elsewhere, row.in_stock) == (D("60"), D("0"))

        # The tracer itself: every draw's in_pnl is what the old per-draw
        # `order_id IN (every P&L order)` said, and no order was asked twice.
        receipts = (
            await db.execute(
                select(InventoryLineCost.line_id, InventoryLineCost.item_id)
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryLineCost.transaction_id,
                )
                .where(InventoryTransaction.purchase_order_id == purchase_order.id)
            )
        ).all()
        tracer = pnl_purchase_orders._Tracer(
            db, {line: (purchase_order.id, item) for line, item in receipts}
        )
        asked: list[uuid.UUID] = []
        resolve = tracer._resolve_in_pnl

        async def counting(order_ids):
            asked.extend(o for o in order_ids if o not in tracer.in_pnl)
            await resolve(order_ids)

        tracer._resolve_in_pnl = counting
        await tracer.run()

        every_pnl_order = set(
            (await db.execute(select(Order.id).where(in_pnl_clause()))).scalars()
        )
        ours = {o.id for o in orders.values()}
        seen = [
            consumer
            for draws in tracer.draws.values()
            for _qty, consumer in draws
            if consumer.order_id is not None
        ]
        assert {c.order_id for c in seen} == ours
        for consumer in seen:
            assert consumer.in_pnl == (consumer.order_id in every_pnl_order)
        assert {o for o in ours if tracer.in_pnl[o]} == {
            orders["P"].id,
            orders["F"].id,
        }
        # Each order asked about exactly once, though P is met in two rounds:
        # the PO's layer, and again through R's return.
        assert sorted(asked) == sorted(ours)
        assert sum(1 for c in seen if c.order_id == orders["P"].id) == 3
        await db.rollback()
