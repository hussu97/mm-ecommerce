"""
A small shop for local-first counter tests, on a real Postgres.

`build_world` creates a branch with its default warehouse and live inventory, a
5% VAT group, a category, two products (one with a recipe), a menu tree that
puts them on the branch's register, a cash and a card method, a cashier assigned
to the branch, a paired device with a ticket prefix and an open till on it.

`device_sale` then plays the register: it fetches the branch's bundle through
`counter_bundle_service.serve`, prices the check with the pure engine
(`counter_pricing.price_check`) against that bundle exactly as the Swift port
does, and returns the `CounterSaleRequest` the device would sync.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.core.security import create_access_token
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.category import Category
from app.models.device import Device
from app.models.inventory import InventoryItem, Warehouse
from app.models.inventory_v2 import BranchInventorySettings
from app.models.menu import MenuGroup, MenuGroupProduct
from app.models.payment_method import PaymentMethod
from app.models.product import Product
from app.models.role import UserBranch
from app.models.tax import Tax, TaxGroup, TaxGroupTax
from app.models.till import Till, TillStatusEnum
from app.models.user import User
from app.schemas.pos_counter import CounterSaleRequest
from app.services.inventory import recipe_service
from app.services.inventory.recipe_service import RecipeLineInput
from app.services.pos import business_day_service, counter_bundle_service
from app.services.pos import counter_pricing as cp

#: A build at or above `COUNTER_LOCAL_FIRST_MIN_BUILD`.
NEW_BUILD = "999999"


@dataclass
class World:
    branch_id: uuid.UUID
    device_id: uuid.UUID
    till_id: uuid.UUID
    cashier_id: uuid.UUID
    cash_id: uuid.UUID
    card_id: uuid.UUID
    brownie_id: uuid.UUID
    cookie_id: uuid.UUID
    category_id: uuid.UUID
    ingredient_id: uuid.UUID
    tax_id: uuid.UUID
    business_date: str
    reference: str


async def build_world(
    db, *, reference: str | None = None, inventory: bool = False
) -> World:
    """A fresh shop. `inventory=True` turns on live stock and sales
    consumption — the caller must then `purge_inventory` afterwards, because
    posted movements are append-only and a later test (`test_inventory_triggers`)
    assumes it owns the first posting sequence."""
    tag = uuid.uuid4().hex[:10]
    branch = Branch(name=f"counter {tag}", reference=reference or f"L{tag[:8]}")
    db.add(branch)
    await db.flush()
    db.add(Warehouse(branch_id=branch.id, name="Default", is_default=True))
    if inventory:
        db.add(
            BranchInventorySettings(
                branch_id=branch.id,
                inventory_enabled=True,
                sales_consumption_enabled=True,
                allow_negative_stock=True,
                go_live_at=utcnow() - timedelta(days=30),
                go_live_sequence=0,
            )
        )
    tax = Tax(name="VAT 5%", rate=Decimal("0.05"), type="inclusive")
    group = TaxGroup(name=f"VAT {tag}")
    category = Category(name=f"Cookies {tag}", slug=f"cookies-{tag}")
    cashier = User(
        email=f"cashier-{tag}@example.com",
        hashed_password="x",
        is_staff=True,
        is_active=True,
    )
    cash = PaymentMethod(name="Cash", code=f"cash-{tag}", type="cash")
    card = PaymentMethod(name="Card", code=f"card-{tag}", type="card")
    ingredient = InventoryItem(
        sku=f"LF-{tag}",
        name="Sugar",
        kind="raw_material",
        tracking_mode="stocked",
        storage_unit="g",
        ingredient_unit="g",
        storage_to_ingredient_factor=Decimal("1"),
    )
    db.add_all([tax, group, category, cashier, cash, card, ingredient])
    await db.flush()
    db.add(TaxGroupTax(tax_group_id=group.id, tax_id=tax.id))
    brownie = Product(
        name="Brownie",
        slug=f"brownie-{tag}",
        sku=f"BR-{tag}",
        base_price=Decimal("21.00"),
        tax_group_id=group.id,
    )
    cookie = Product(
        name="Cookie",
        slug=f"cookie-{tag}",
        sku=f"CK-{tag}",
        base_price=Decimal("12.50"),
        tax_group_id=group.id,
        category_id=category.id,
    )
    db.add_all([brownie, cookie])
    db.add(UserBranch(user_id=cashier.id, branch_id=branch.id))
    await db.flush()
    root = MenuGroup(
        name="Counter", root_kind="branch", branch_id=branch.id, is_active=True
    )
    db.add(root)
    await db.flush()
    db.add_all(
        [
            MenuGroupProduct(group_id=root.id, product_id=brownie.id),
            MenuGroupProduct(group_id=root.id, product_id=cookie.id),
        ]
    )
    await recipe_service.draft_and_activate(
        db,
        kind="product",
        owner_id=brownie.id,
        lines=[RecipeLineInput(item_id=ingredient.id, quantity=Decimal("2"))],
        user_id=cashier.id,
    )
    device = Device(
        name=f"Till {tag}",
        reference=f"D{tag}",
        type="cashier",
        branch_id=branch.id,
        status="used",
        token_hash=uuid.uuid4().hex,
    )
    db.add(device)
    await db.flush()
    business_date = await business_day_service.current_business_date(db, branch)
    till = Till(
        branch_id=branch.id,
        device_id=device.id,
        user_id=cashier.id,
        business_date=business_date,
        status=TillStatusEnum.OPEN.value,
        opening_amount=Decimal("100.00"),
        estimated_cash=Decimal("100.00"),
        variance=Decimal("0.00"),
        opened_at=utcnow() - timedelta(hours=1),
    )
    db.add(till)
    await db.flush()
    return World(
        branch_id=branch.id,
        device_id=device.id,
        till_id=till.id,
        cashier_id=cashier.id,
        cash_id=cash.id,
        card_id=card.id,
        brownie_id=brownie.id,
        cookie_id=cookie.id,
        category_id=category.id,
        ingredient_id=ingredient.id,
        tax_id=tax.id,
        business_date=business_date,
        reference=branch.reference,
    )


def attestation_for(cashier_id: uuid.UUID) -> str:
    return create_access_token(
        user_id=str(cashier_id),
        email="cashier@example.com",
        expires_delta=timedelta(hours=12),
    )


async def device_sale(
    db,
    world: World,
    *,
    lines: list[tuple[uuid.UUID, int]],
    seq: int = 1,
    method: str = "cash",
    coupon_id: uuid.UUID | None = None,
    closed_at: datetime | None = None,
    bundle_hash: str | None = None,
    attestation: str | None = "auto",
    state: str = "closed",
    fire_kitchen: bool = True,
) -> CounterSaleRequest:
    """What the register would sync for a check of `lines` (`(product, qty)`)."""
    device = await db.get(Device, world.device_id)
    served = await counter_bundle_service.serve(
        db, device=device, build_number=NEW_BUILD
    )
    ctx = counter_bundle_service.context_from_payload(served.body)
    products = {p["id"]: p for p in served.body["products"]}

    closed = closed_at or datetime.now(timezone.utc)
    opened = closed - timedelta(minutes=3)
    check = [
        cp.CheckLine(
            id=uuid.uuid4(),
            product_id=pid,
            quantity=qty,
            unit_price=Decimal(products[str(pid)]["base_price"]),
        )
        for pid, qty in lines
    ]
    pricing = cp.price_check(ctx, check, closed - timedelta(minutes=1), coupon_id)
    wire = cp.pricing_to_wire(pricing)
    by_id = {line["id"]: line for line in wire["lines"]}
    total = Decimal(wire["total"])
    method_id = world.cash_id if method == "cash" else world.card_id
    promo = wire["promotion"]
    kitchen = []
    if fire_kitchen:
        kitchen = [
            {
                "sequence": 1,
                "kitchen_flow_id": None,
                "line_ids": [str(line.id) for line in check],
                "sent_at": (opened + timedelta(minutes=1)).isoformat(),
                "printed_at": (opened + timedelta(minutes=1, seconds=2)).isoformat(),
            }
        ]
    payload = {
        "id": str(uuid.uuid4()),
        "branch_id": str(world.branch_id),
        "device_id": str(world.device_id),
        "till_id": str(world.till_id),
        "cashier_id": str(world.cashier_id),
        "staff_attestation": (
            attestation_for(world.cashier_id) if attestation == "auto" else attestation
        ),
        "ticket_prefix": served.envelope.ticket_prefix,
        "ticket_seq": seq,
        "display_number": f"{served.envelope.ticket_prefix}-{seq:04d}",
        "business_date": world.business_date,
        "opened_at": opened.isoformat(),
        "priced_at": (closed - timedelta(minutes=1)).isoformat(),
        "closed_at": closed.isoformat(),
        "bundle_hash": bundle_hash or served.hash,
        "engine_version": cp.ENGINE_VERSION,
        "state": state,
        "coupon_promotion_id": str(coupon_id) if coupon_id else None,
        "lines": [
            {
                "id": str(line.id),
                "product_id": str(line.product_id),
                "quantity": line.quantity,
                "sent_to_kitchen_at": (opened + timedelta(minutes=1)).isoformat()
                if fire_kitchen
                else None,
                "totals": {
                    k: by_id[str(line.id)][k]
                    for k in (
                        "base_price",
                        "options_price",
                        "unit_price",
                        "gross",
                        "discount",
                        "total_price",
                        "tax_amount",
                        "tax_exclusive_total",
                        "tax_exclusive_unit",
                    )
                },
            }
            for line in check
        ],
        "tenders": (
            []
            if state == "void"
            else [
                {
                    "id": str(uuid.uuid4()),
                    "idempotency_key": uuid.uuid4().hex,
                    "payment_method_id": str(method_id),
                    "amount": str(total),
                    "tendered": str(total + Decimal("5")) if method == "cash" else None,
                    "taken_at": (closed - timedelta(seconds=20)).isoformat(),
                }
            ]
        ),
        "totals": {
            "subtotal": wire["subtotal"],
            "discount_total": wire["discount_total"],
            "tax_total": wire["tax_total"],
            "total_excl_tax": wire["total_excl_tax"],
            "rounding": wire["rounding"],
            "total": wire["total"],
            "promotion_id": promo["id"] if promo else None,
            "promotion_amount": promo["amount"] if promo else "0.00",
            "taxes": wire["taxes"],
        },
        "kitchen_tickets": kitchen,
        "receipt_printed_at": closed.isoformat(),
    }
    return CounterSaleRequest.model_validate(payload)


async def purge_inventory(Session, world: World) -> None:
    """Remove the stock movements a live-inventory world posted. Posted
    movements are append-only, so the triggers are bypassed the way the other
    inventory tests tear down."""
    from sqlalchemy import select, text

    from app.models.inventory import (
        InventoryCostLayer,
        InventoryCostLayerConsumption,
        InventoryLevel,
        InventoryLineCost,
        InventoryTransaction,
        InventoryTransactionItem,
    )
    from app.models.inventory_v2 import InventorySourceEvent

    async with Session() as db:
        await db.execute(text("SET session_replication_role = 'replica'"))
        transactions = select(InventoryTransaction.id).where(
            InventoryTransaction.branch_id == world.branch_id
        )
        lines = select(InventoryTransactionItem.id).where(
            InventoryTransactionItem.transaction_id.in_(transactions)
        )
        await db.execute(
            InventoryCostLayerConsumption.__table__.delete().where(
                InventoryCostLayerConsumption.consuming_line_id.in_(lines)
            )
        )
        for model in (InventoryCostLayer, InventoryLineCost, InventorySourceEvent):
            await db.execute(
                model.__table__.delete().where(model.branch_id == world.branch_id)
            )
        await db.execute(
            InventoryTransactionItem.__table__.delete().where(
                InventoryTransactionItem.transaction_id.in_(transactions)
            )
        )
        await db.execute(
            InventoryTransaction.__table__.delete().where(
                InventoryTransaction.branch_id == world.branch_id
            )
        )
        await db.execute(
            InventoryLevel.__table__.delete().where(
                InventoryLevel.item_id == world.ingredient_id
            )
        )
        await db.execute(
            BranchInventorySettings.__table__.delete().where(
                BranchInventorySettings.branch_id == world.branch_id
            )
        )
        await db.execute(text("SET session_replication_role = 'origin'"))
        await db.commit()
