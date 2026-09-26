"""
A custom order end to end, on a real Postgres: taken, docket claimed, packed
(its own recipe consumed), finished by a third-party courier.

Pins what only the database can say: the order's money is written by the
register's one money writer (lines, card-fee charge, VAT, legal entity), the
card-fee line covers the processor's fee on itself, the kitchen docket is
claimed exactly once, packing posts the order's recipe (not a product recipe)
to the ledger and may take it negative, and the hand-over stamps the day every
report files the order under.

Runs only when `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import os
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
)


async def test_a_custom_order_is_taken_packed_and_finished():
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models.business_settings import BusinessSettings
    from app.models.charge import Charge
    from app.models.inventory import (
        InventoryCategory,
        InventoryItem,
        InventoryTransaction,
        InventoryTransactionItem,
    )
    from app.models.order import OrderStatusEnum
    from app.models.order_delivery import OrderDelivery
    from app.models.product import Product
    from app.models.user import User
    from app.schemas.custom_order import CustomOrderCreate
    from app.services.orders import custom_order_service
    from tests.integration._counter_world import build_world, purge_inventory

    engine = create_async_engine(DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    tag = uuid.uuid4().hex[:8]

    async with Session() as db:
        world = await build_world(db, inventory=True)
        brownie = await db.get(Product, world.brownie_id)
        product = Product(
            name="Cake - Customer Specification",
            slug=f"custom-cake-{tag}",
            sku=f"FG-{tag}",
            base_price=Decimal("0"),
            pricing_method="open",
            consumes_stock=False,
            tax_group_id=brownie.tax_group_id,
        )
        category = InventoryCategory(
            name=f"Customized Cake Raw Materials {tag}", reference=f"cc-{tag}"
        )
        db.add_all([product, category])
        await db.flush()
        ganache = InventoryItem(
            sku=f"GN-{tag}",
            name="Dark ganache",
            kind="semi_finished",
            tracking_mode="stocked",
            storage_unit="kg",
            ingredient_unit="g",
            storage_to_ingredient_factor=Decimal("1000"),
            category_id=category.id,
        )
        db.add(ganache)
        settings = (await db.execute(select(BusinessSettings).limit(1))).scalar_one()
        previous = (
            settings.custom_orders_branch_id,
            settings.custom_orders_product_id,
            settings.custom_orders_inventory_category_id,
        )
        settings.custom_orders_branch_id = world.branch_id
        settings.custom_orders_product_id = product.id
        settings.custom_orders_inventory_category_id = category.id
        # The card-fee charge is taxed like the cake (migration 295 reads FG0119).
        fee_charge = (
            await db.execute(
                select(Charge).where(
                    Charge.reference == custom_order_service.CARD_FEE_CHARGE_REFERENCE
                )
            )
        ).scalar_one()
        previous_fee_group = fee_charge.tax_group_id
        fee_charge.tax_group_id = brownie.tax_group_id
        await db.commit()

    try:
        async with Session() as db:
            user = await db.get(User, world.cashier_id)
            order = await custom_order_service.create(
                db,
                CustomOrderCreate(
                    lines=[
                        {
                            "title": "3-tier red velvet",
                            "quantity": 1,
                            "unit_price": "100",
                        },
                        {"title": "Cupcakes", "quantity": 2, "unit_price": "50"},
                    ],
                    delivery_date=date.today() + timedelta(days=3),
                    customer={
                        "name": "Farah Test",
                        "email": "farah@example.com",
                        "phone": "0501234567",
                    },
                    address={
                        "latitude": 25.34,
                        "longitude": 55.42,
                        "address_line_1": "Al Majaz 3",
                        "unit_number": "Villa 14",
                    },
                    payment_type="card",
                    card_fee_mode="separate_line",
                    recipe=[{"item_id": str(ganache.id), "quantity": "250"}],
                ),
                user=user,
                via="admin",
            )
            await db.commit()
            order, custom = await custom_order_service.get_by_id(db, order.id)

            assert order.order_number.startswith("CO-")
            assert order.status == OrderStatusEnum.CONFIRMED
            assert order.customer_phone == "+971501234567"
            assert order.delivery_method.value == "delivery"
            assert order.payment_method == "card"
            assert order.legal_entity_id is not None
            # 200 of cake plus a fee line sized to the processor's fee on the lot.
            fee_line = order.order_charges[0].amount
            assert order.subtotal == Decimal("200.00")
            assert order.total == Decimal("200.00") + fee_line
            assert abs(order.payment_fee - fee_line) <= Decimal("0.01")
            assert order.vat_amount > 0
            # Not yet handed over: filed under the delivery date it was promised.
            assert order.reporting_at == order.promised_at

            # The docket is claimed once; the order is then at the POS.
            assert await custom_order_service.claim_print(db, order, user=user)
            assert not await custom_order_service.claim_print(db, order, user=user)
            assert order.status == OrderStatusEnum.ARRIVED_AT_POS
            await db.commit()

            # Packing consumes the order's own recipe, in storage units.
            await custom_order_service.pack(db, order, user=user, via="pos")
            await db.commit()
            moved = (
                (
                    await db.execute(
                        select(InventoryTransactionItem.quantity)
                        .join(
                            InventoryTransaction,
                            InventoryTransaction.id
                            == InventoryTransactionItem.transaction_id,
                        )
                        .where(
                            InventoryTransaction.order_id == order.id,
                            InventoryTransactionItem.item_id == ganache.id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert moved, "packing posted no consumption for the recipe"

            # A third-party courier: its fee is the delivery cost; delivered.
            order, custom = await custom_order_service.get_by_id(db, order.id)
            await custom_order_service.finish_third_party(
                db, order, courier_fee=Decimal("25"), user=user
            )
            await db.commit()
            order, custom = await custom_order_service.get_by_id(db, order.id)
            delivery = (
                await db.execute(
                    select(OrderDelivery).where(OrderDelivery.order_id == order.id)
                )
            ).scalar_one()
            assert order.status == OrderStatusEnum.DELIVERED
            assert delivery.provider == "third_party"
            assert delivery.cost_total == Decimal("25.00")
            assert order.delivered_at is not None
            assert order.business_date is not None
            assert order.is_pos is False and order.pos_status is None
            assert order.reporting_at == order.delivered_at

            response = await custom_order_service.to_response(db, order, custom)
            assert response.actions.invoice_unavailable_reason is None
            assert response.recipe[0].quantity == Decimal("250")

            # Cancelled straight after it was taken, and after its docket
            # printed: both before packing, so nothing was consumed.
            for claim_first in (False, True):
                fresh = await custom_order_service.create(
                    db,
                    CustomOrderCreate(
                        lines=[
                            {"title": "Called off", "quantity": 1, "unit_price": "80"}
                        ],
                        delivery_date=date.today() + timedelta(days=5),
                    ),
                    user=user,
                    via="pos",
                )
                await db.commit()
                fresh, fresh_custom = await custom_order_service.get_by_id(db, fresh.id)
                if claim_first:
                    await custom_order_service.claim_print(db, fresh, user=user)
                    assert fresh.status == OrderStatusEnum.ARRIVED_AT_POS
                response = await custom_order_service.to_response(
                    db, fresh, fresh_custom
                )
                assert response.actions.can_cancel
                await custom_order_service.cancel(db, fresh, user=user, via="pos")
                await db.commit()
                assert fresh.status == OrderStatusEnum.CANCELLED
                assert fresh.business_date is not None
    finally:
        await purge_inventory(Session, world)
        async with Session() as db:
            settings = (
                await db.execute(select(BusinessSettings).limit(1))
            ).scalar_one()
            (
                settings.custom_orders_branch_id,
                settings.custom_orders_product_id,
                settings.custom_orders_inventory_category_id,
            ) = previous
            fee_charge = (
                await db.execute(
                    select(Charge).where(
                        Charge.reference
                        == custom_order_service.CARD_FEE_CHARGE_REFERENCE
                    )
                )
            ).scalar_one()
            fee_charge.tax_group_id = previous_fee_group
            await db.commit()
        await engine.dispose()
