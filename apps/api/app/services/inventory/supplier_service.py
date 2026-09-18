"""
Suppliers, their contacts, and the supplier↔item mapping.

Two rules live here rather than in the router:

1. A **contact** must be reachable — a name with neither an email nor a phone is
   refused (also a DB CHECK, this is the friendly message).
2. An item may be **mapped to a supplier only if it is purchased** — its kind is
   a raw material, packaging or resale good and it owns no recipe. A recipe means
   the item is produced, not bought, so it can never appear on a purchase order.

Also the one piece of purchase money maths: splitting a VAT-inclusive line total
into the recoverable VAT slice and the net. The VAT stays inside the item's cost
either way (see `Supplier.is_vat_deductible`); the split is recorded for the
reclaim report, not deducted from inventory value.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.core.money import money as _money
from app.core.money import unit_cost as _c
from app.models.inventory import (
    InventoryItem,
    Supplier,
    SupplierContact,
    SupplierItem,
)
from app.models.inventory_v2 import Recipe

__all__ = [
    "PURCHASE_VAT_RATE",
    "PURCHASABLE_KINDS",
    "VatSplit",
    "split_line_vat",
    "assert_item_purchasable",
    "create_supplier",
    "update_supplier",
    "replace_contacts",
    "set_supplier_items",
]

#: UAE standard-rate VAT, the only rate MM's suppliers charge.
PURCHASE_VAT_RATE = Decimal("0.05")

#: The item kinds that are bought rather than produced (mirrors
#: ``recipe_service._PURCHASED_ITEM_KINDS``).
PURCHASABLE_KINDS = frozenset({"raw_material", "packaging", "resale_good"})


@dataclass(slots=True)
class VatSplit:
    unit_cost: Decimal  # gross, per storage unit — the FIFO layer cost
    vat_amount: Decimal  # recoverable VAT slice of the line total (0 if not)
    net_total: Decimal  # entered_total − vat_amount
    total: Decimal  # gross line total (== entered_total)


def split_line_vat(
    entered_total: Decimal, quantity: Decimal, *, is_vat_deductible: bool
) -> VatSplit:
    """Split a VAT-inclusive line total.

    The unit cost the FIFO layer is valued at is always **gross** — the tax is
    part of what the stock cost. When the supplier is VAT-deductible the
    recoverable slice is recorded separately: ``gross − gross/(1+rate)``.
    """
    gross = _money(entered_total)
    qty = Decimal(str(quantity))
    if qty <= 0:
        raise BadRequestError("A purchase line needs a positive quantity")
    if is_vat_deductible:
        net = _money(gross / (Decimal("1") + PURCHASE_VAT_RATE))
        vat = _money(gross - net)
    else:
        net = gross
        vat = Decimal("0.00")
    return VatSplit(
        unit_cost=_c(gross / qty),
        vat_amount=vat,
        net_total=net,
        total=gross,
    )


async def assert_item_purchasable(db: AsyncSession, item: InventoryItem) -> None:
    """Refuse mapping an item that is produced (wrong kind, or owns a recipe)."""
    if item.kind not in PURCHASABLE_KINDS:
        raise BadRequestError(
            f"{item.name} is a {item.kind} item; only raw materials, packaging "
            "and resale goods can be supplied by a vendor"
        )
    recipe = (
        await db.execute(
            select(Recipe.id).where(Recipe.inventory_item_id == item.id).limit(1)
        )
    ).first()
    if recipe is not None:
        raise BadRequestError(
            f"{item.name} is made from a recipe, so it cannot be mapped to a "
            "supplier — it is produced, not purchased"
        )


async def create_supplier(db: AsyncSession, data) -> Supplier:
    supplier = Supplier(
        **data.model_dump(exclude={"contacts"}),
    )
    db.add(supplier)
    await db.flush()
    for contact in data.contacts:
        db.add(SupplierContact(supplier_id=supplier.id, **contact.model_dump()))
    await db.flush()
    await db.refresh(supplier)
    return supplier


async def update_supplier(db: AsyncSession, supplier: Supplier, data) -> Supplier:
    payload = data.model_dump(exclude={"contacts"}, exclude_unset=True)
    for key, value in payload.items():
        setattr(supplier, key, value)
    if data.contacts is not None:
        await replace_contacts(db, supplier, data.contacts)
    await db.flush()
    await db.refresh(supplier)
    return supplier


async def replace_contacts(db: AsyncSession, supplier: Supplier, contacts) -> None:
    """Swap the supplier's whole contact set for the one given."""
    existing = (
        (
            await db.execute(
                select(SupplierContact).where(
                    SupplierContact.supplier_id == supplier.id
                )
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        await db.delete(row)
    await db.flush()
    for contact in contacts:
        db.add(SupplierContact(supplier_id=supplier.id, **contact.model_dump()))
    await db.flush()


async def set_supplier_items(
    db: AsyncSession, supplier_id: uuid.UUID, entries
) -> list[SupplierItem]:
    """Replace a supplier's item mappings, enforcing the purchased-item rule."""
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None:
        raise NotFoundError("Supplier not found")
    for entry in entries:
        item = await db.get(InventoryItem, entry.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {entry.item_id} not found")
        await assert_item_purchasable(db, item)
    existing = (
        (
            await db.execute(
                select(SupplierItem).where(SupplierItem.supplier_id == supplier_id)
            )
        )
        .scalars()
        .all()
    )
    for row in existing:
        await db.delete(row)
    await db.flush()
    for entry in entries:
        db.add(SupplierItem(supplier_id=supplier_id, **entry.model_dump()))
    await db.flush()
    return (
        (
            await db.execute(
                select(SupplierItem).where(SupplierItem.supplier_id == supplier_id)
            )
        )
        .scalars()
        .all()
    )
