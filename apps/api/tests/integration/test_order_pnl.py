"""Order P&L end to end against a real Postgres.

Every figure below is worked by hand in the comments, so a change to the
arithmetic has to change a number a human can check. Pins:

* the VAT lines — amounts as billed, output VAT from the order's own frozen
  figures, fee VAT reclaimed only under a registered entity, COGS at net cost;
* which orders count — a sale, a charged cancellation, and not an uncharged one;
* COGS from the FIFO projection, and blank (not zero) when no stock was drawn;
* the report equals the sum of its orders to the fils;
* period charges: an itemised-VAT monthly fee, and noon's statement fee
  booked only as the true-up over what its orders already carry.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.aggregator import AggregatorOrder, AggregatorStatementLine
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCostLayer,
    InventoryCostLayerConsumption,
    InventoryItem,
    InventoryLevel,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.legal_entity import LegalEntity
from app.models.order import DeliveryMethodEnum, Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery
from app.services.aggregators.period_charges import period_charges
from app.services.inventory import inventory_service
from app.services.orders import order_pnl, pnl_report

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

D = Decimal
#: A shop day nothing else in the test database trades on.
DAY = "2031-01-15"
AT = datetime(2031, 1, 15, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
async def engine():
    engine = create_async_engine(DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def world(engine):
    """A branch, two entities, one costed item and six orders (A–F)."""
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]
    async with Session() as db:
        branch = Branch(name=f"pnl {tag}", reference=f"pnl-{tag}")
        db.add(branch)
        await db.flush()
        warehouse = Warehouse(branch_id=branch.id, name="Default", is_default=True)
        db.add(warehouse)
        db.add(
            BranchInventorySettings(
                branch_id=branch.id,
                inventory_enabled=True,
                go_live_at=utcnow(),
                go_live_sequence=0,
            )
        )
        registered = LegalEntity(
            reference=f"reg-{tag}", legal_name="Reg", brand_name="Reg"
        )
        unregistered = LegalEntity(
            reference=f"unreg-{tag}",
            legal_name="Unreg",
            brand_name="Unreg",
            vat_registered=False,
        )
        item = InventoryItem(
            sku=f"pnl-{tag}",
            name="Butter",
            kind="raw_material",
            tracking_mode="stocked",
            storage_unit="gram",
            ingredient_unit="gram",
            storage_to_ingredient_factor=D("1"),
        )
        box = InventoryItem(
            sku=f"pnl-box-{tag}",
            name="Box",
            kind="packaging",
            tracking_mode="stocked",
            storage_unit="piece",
            ingredient_unit="piece",
            storage_to_ingredient_factor=D("1"),
        )
        db.add_all([registered, unregistered, item, box])
        await db.flush()

        def order(suffix, **over):
            base = dict(
                order_number=f"PNL-{tag}-{suffix}",
                email="pnl@example.com",
                branch_id=branch.id,
                legal_entity_id=registered.id,
                source="aggregator",
                status=OrderStatusEnum.DELIVERED,
                delivery_method=DeliveryMethodEnum.DELIVERY,
                subtotal=D("0"),
                total=D("0"),
                vat_rate=D("0.05"),
                vat_amount=D("0"),
                total_excl_vat=D("0"),
                created_at=AT,
            )
            base.update(over)
            return Order(**base)

        orders = {
            # A — website delivery. Menu 105, 10.50 coupon, 21 delivery → 115.50
            # charged, VAT 5.50 (a round fixture figure — the P&L reads the
            # order's frozen VAT as given). 21.00 refunded, card fee 4.20,
            # courier 10.50, 3 g of butter at 2.00 = 6.00 COGS.
            "A": order(
                "A",
                source="online",
                subtotal=D("105.00"),
                discount_amount=D("10.50"),
                delivery_fee=D("21.00"),
                total=D("115.50"),
                vat_amount=D("5.50"),
                total_excl_vat=D("110.00"),
                refunded_amount=D("21.00"),
                payment_fee=D("4.20"),
                payment_method="card",
            ),
            # B — Talabat. 42 basket (2.00 VAT), commission 12.60, payment 0.84,
            # Pro fee 4.20, all VAT-inclusive. No stock drawn.
            "B": order(
                "B",
                aggregator_channel="Talabat",
                subtotal=D("42.00"),
                total=D("42.00"),
                vat_amount=D("2.00"),
                total_excl_vat=D("40.00"),
                aggregator_fee=D("12.60"),
                payment_fee=D("0.84"),
                marketing_fee=D("4.20"),
            ),
            # C — counter sale under the non-registered entity: 50 menu, 5 off,
            # no VAT either way, card fee 1.50 is a full cost.
            "C": order(
                "C",
                source="cashier",
                pos_status="closed",
                closed_at=AT,
                legal_entity_id=unregistered.id,
                delivery_method=DeliveryMethodEnum.PICKUP,
                subtotal=D("50.00"),
                discount_amount=D("5.00"),
                total=D("45.00"),
                vat_rate=D("0"),
                total_excl_vat=D("45.00"),
                payment_fee=D("1.50"),
            ),
            # D — noon, cancelled but charged: 10.50 + 0.84 + 0.84 billed.
            "D": order(
                "D",
                aggregator_channel="Noon Food",
                status=OrderStatusEnum.CANCELLED,
                aggregator_fee=D("10.50"),
                payment_fee=D("0.84"),
                cancellation_fee=D("0.84"),
            ),
            # E — Keeta, cancelled, a stale commission stamped but the statement
            # pays out (net payable > 0): not charged, not in the P&L.
            "E": order(
                "E",
                aggregator_channel="Keeta 2.0",
                status=OrderStatusEnum.CANCELLED,
                subtotal=D("95.00"),
                total=D("95.00"),
                aggregator_fee=D("22.75"),
            ),
            # F — Talabat, cancelled, no cancellation fee but the statement bills
            # the commission back (net payable −22.05).
            "F": order(
                "F",
                aggregator_channel="Talabat",
                status=OrderStatusEnum.CANCELLED,
                subtotal=D("70.00"),
                total=D("70.00"),
                aggregator_fee=D("22.05"),
            ),
        }
        db.add_all(orders.values())
        await db.flush()
        db.add(
            OrderDelivery(
                order_id=orders["A"].id,
                provider="lalamove",
                zone_name="Marina",
                cost_total=D("10.50"),
            )
        )
        for key, channel, net, statement in (
            ("D", "noon", D("-12.18"), f"NOON-{tag}"),
            ("E", "keeta", D("66.35"), None),
            ("F", "talabat", D("-22.05"), None),
        ):
            db.add(
                AggregatorOrder(
                    channel=channel,
                    external_order_id=f"{tag}-{key}",
                    mm_order_id=orders[key].id,
                    net_payable=net,
                    statement_id=statement,
                )
            )
        # Period charges on the day: Deliveroo's monthly fee with its VAT on its
        # own line, and noon's statement payment fee of 2.94 — of which order D
        # already carries 0.84, so the true-up is 2.10 (2.00 + 0.10 VAT).
        monthly = "Brand level monthly platform fee of AED 200"
        for n, (channel, category, line_type, amount, desc, sid) in enumerate(
            (
                (
                    "deliveroo",
                    "monthly_admin_fee",
                    "adjustment",
                    "-190.48",
                    monthly,
                    None,
                ),
                ("deliveroo", "commission_vat", "vat", "-9.52", monthly, None),
                ("deliveroo", "net_payable", "net_payable", "-200.00", monthly, None),
                ("noon", "payment_fee", "fee", "-2.94", "Payment fee", f"NOON-{tag}"),
            )
        ):
            db.add(
                AggregatorStatementLine(
                    channel=channel,
                    source_key=f"pnl-{tag}-{n}",
                    statement_id=sid,
                    external_order_id=None,
                    line_date=DAY,
                    line_type=line_type,
                    fee_category=category,
                    description=desc,
                    amount=D(amount),
                    grain="summary",
                )
            )
        await db.flush()

        for kind, lines, order_id in (
            (
                InventoryTransactionTypeEnum.PURCHASING,
                [(item.id, "10", "2"), (box.id, "5", "1.05")],
                None,
            ),
            (
                InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS,
                [(item.id, "3", "0"), (box.id, "1", "0")],
                orders["A"].id,
            ),
        ):
            transaction = InventoryTransaction(
                reference=await inventory_service.next_reference(db, kind.value),
                type=kind.value,
                status=TransactionStatusEnum.DRAFT.value,
                branch_id=branch.id,
                warehouse_id=warehouse.id,
                business_date=DAY,
                order_id=order_id,
                items=[
                    InventoryTransactionItem(
                        item_id=item_id,
                        quantity=D(quantity),
                        unit="storage",
                        conversion_factor=D("1"),
                        unit_cost=D(unit_cost),
                    )
                    for item_id, quantity, unit_cost in lines
                ],
            )
            db.add(transaction)
            await db.flush()
            await inventory_service.post_transaction(
                db, transaction=transaction, user=None
            )
        await db.commit()
        ids = {
            "tag": tag,
            "branch": branch.id,
            "item": item.id,
            "box": box.id,
            "entities": (registered.id, unregistered.id),
            "orders": {k: o.id for k, o in orders.items()},
        }

    yield ids

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        order_ids = list(ids["orders"].values())
        txn_ids = select(InventoryTransaction.id).where(
            InventoryTransaction.branch_id == ids["branch"]
        )
        line_ids = select(InventoryTransactionItem.id).where(
            InventoryTransactionItem.transaction_id.in_(txn_ids)
        )
        for stmt in (
            InventoryCostLayerConsumption.__table__.delete().where(
                InventoryCostLayerConsumption.consuming_line_id.in_(line_ids)
            ),
            InventoryCostLayer.__table__.delete().where(
                InventoryCostLayer.branch_id == ids["branch"]
            ),
            InventoryLineCost.__table__.delete().where(
                InventoryLineCost.branch_id == ids["branch"]
            ),
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(txn_ids)
            ),
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == ids["branch"]
            ),
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id.in_([ids["item"], ids["box"]])
            ),
            InventoryItem.__table__.delete().where(
                InventoryItem.id.in_([ids["item"], ids["box"]])
            ),
            AggregatorStatementLine.__table__.delete().where(
                AggregatorStatementLine.source_key.like(f"pnl-{ids['tag']}-%")
            ),
            AggregatorOrder.__table__.delete().where(
                AggregatorOrder.mm_order_id.in_(order_ids)
            ),
            OrderDelivery.__table__.delete().where(
                OrderDelivery.order_id.in_(order_ids)
            ),
            Order.__table__.delete().where(Order.id.in_(order_ids)),
            LegalEntity.__table__.delete().where(LegalEntity.id.in_(ids["entities"])),
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id == ids["branch"]
            ),
            Warehouse.__table__.delete().where(Warehouse.branch_id == ids["branch"]),
            Branch.__table__.delete().where(Branch.id == ids["branch"]),
        ):
            await db.execute(stmt)
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()


async def _pnl(engine, order_id):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        return await order_pnl.for_order(db, order_id)


async def test_a_website_order_shows_its_vat_as_lines(engine, world):
    p = await _pnl(engine, world["orders"]["A"])
    assert p.channel == "website_delivery" and p.is_sale
    assert p.gmv == D("105.00")  # 115.50 charged + 10.50 coupon − 21.00 delivery
    assert p.refunds == D("21.00")
    assert p.output_vat == D("4.50")  # 5.50 − 21.00 × 5/105
    assert p.net_revenue == D("79.50")
    # 3 g butter × 2.00 → 5.71 net, one box × 1.05 → 1.00 net (5/105 VAT off)
    assert p.cogs_raw == D("5.71")
    assert p.cogs_packaging == D("1.00")
    assert p.cogs_produced == D("0.00") and p.cogs_resale == D("0.00")
    assert p.cogs == D("6.71")  # exactly the sum of its kinds
    assert p.pc1 == D("72.79")
    assert p.delivery_fees == D("21.00")  # no VAT on it
    assert p.payment_fees == D("4.20")
    assert p.delivery_cost == D("10.50")
    assert p.fees_vat == D("0.70")  # (4.20 + 10.50) × 5/105
    assert p.pc2 == D("79.79")
    assert p.discounts == D("10.50")
    assert p.pc3 == D("69.29")
    assert p.share(p.pc3) == D("65.99")
    assert p.net_vat == D("3.80")  # 4.50 − 0.70; COGS VAT was reclaimed at purchase


async def test_a_marketplace_order_with_no_stock_has_blank_cogs(engine, world):
    p = await _pnl(engine, world["orders"]["B"])
    assert p.channel == "talabat"
    assert p.cogs is None  # unknown, not free
    assert p.gmv == D("42.00")
    assert p.output_vat == D("2.00")
    assert p.commission == D("12.60")
    assert p.payment_fees == D("0.84")
    assert p.marketplace_fees == D("4.20")
    assert p.delivery_cost == D("0.00")  # the marketplace carries it
    assert p.fees_vat == D("0.84")
    assert p.pc3 == D("23.20")


async def test_an_unregistered_entity_bears_its_fee_vat(engine, world):
    p = await _pnl(engine, world["orders"]["C"])
    assert p.channel == "counter"
    assert p.gmv == D("50.00")
    assert p.discounts == D("5.00")
    assert p.payment_fees == D("1.50")
    assert p.fees_vat == D("0.00")  # nothing reclaimed
    assert p.output_vat == D("0.00")
    assert p.pc3 == D("43.50")


async def test_a_charged_cancellation_is_all_cancellation_charge(engine, world):
    d = await _pnl(engine, world["orders"]["D"])
    assert d.channel == "noon_food" and not d.is_sale
    assert d.gmv == D("0.00")
    assert d.commission == D("0.00") and d.payment_fees == D("0.00")
    assert d.cancellation_charges == D("12.18")  # 10.50 + 0.84 + 0.84 billed
    assert d.fees_vat == D("0.58")
    assert d.pc3 == D("-11.60")
    f = await _pnl(engine, world["orders"]["F"])
    assert f.cancellation_charges == D("22.05")  # billed back by the statement
    assert f.pc3 == D("-21.00")


async def test_an_uncharged_cancellation_is_not_in_the_pnl(engine, world):
    assert await _pnl(engine, world["orders"]["E"]) is None


async def test_the_report_is_the_sum_of_its_orders(engine, world):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        report = await pnl_report.build(
            db, date_from=DAY, date_to=DAY, branch_ids=[world["branch"]]
        )
    assert not report.period_charges_included  # a branch slice drops them
    by_channel = dict(report.channels)
    assert list(by_channel) == ["counter", "website_delivery", "talabat", "noon_food"]
    total = report.total
    assert total.orders == 5
    assert total.charged_cancellations == 2
    assert total.orders_with_cogs == 1
    assert total.gmv == D("197.00")  # 105 + 42 + 50
    assert total.delivery_fees == D("21.00")
    assert total.cogs == D("6.71")
    assert total.cogs_packaging == D("1.00")
    # PC3 = 69.29 + 23.20 + 43.50 − 11.60 − 21.00
    assert total.pc3 == D("103.39")
    assert by_channel["talabat"].pc3 == D("2.20")  # 23.20 − 21.00


async def test_period_charges_book_the_fee_and_the_noon_true_up(engine, world):
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        charges = {
            (c.channel, c.category): c
            for c in await period_charges(db, DAY, DAY)
            if c.first_date == DAY
        }
    monthly = charges[("deliveroo", "monthly_admin_fee")]
    assert monthly.amount == D("200.00") and monthly.input_vat == D("9.52")
    true_up = charges[("noon_food", "payment_fee")]
    assert true_up.is_true_up
    assert true_up.amount == D("2.10")  # 2.94 invoiced − 0.84 on order D
    assert true_up.input_vat == D("0.10")


async def test_an_entity_slice_keeps_period_charges_only_for_the_marketplace_entity(
    engine, world
):
    from app.models.legal_entity import LegalEntity as LE
    from app.services.orders import tax_identity_service

    Session = async_sessionmaker(engine, expire_on_commit=False)
    registered, unregistered = world["entities"]
    async with Session() as db:
        counter_only = await pnl_report.build(
            db, date_from=DAY, date_to=DAY, legal_entity_ids=[unregistered]
        )
        marketplace = await tax_identity_service.resolve(
            db, branch_id=None, source="aggregator"
        )
        assert isinstance(marketplace, LE)
        with_marketplace = await pnl_report.build(
            db, date_from=DAY, date_to=DAY, legal_entity_ids=[marketplace.id]
        )
    # The non-registered counter entity: its one sale, no marketplace charges.
    assert not counter_only.period_charges_included
    assert [code for code, _ in counter_only.channels] == ["counter"]
    assert counter_only.total.pc3 == D("43.50")
    # The entity the marketplace accounts are booked under keeps them.
    assert with_marketplace.period_charges_included
