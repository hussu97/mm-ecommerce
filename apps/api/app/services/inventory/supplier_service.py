"""
Suppliers, their contacts, and the supplier↔item mapping.

Two rules live here rather than in the router:

1. A **contact** must be reachable — a name with neither an email nor a phone is
   refused (also a DB CHECK, this is the friendly message).
2. An item may be **mapped to a supplier only if it is purchased** — its kind is
   a raw material, packaging or resale good and it owns no recipe. A recipe means
   the item is produced, not bought, so it can never appear on a purchase order.

It also keeps a supplier's two registration documents — the VAT (TRN)
certificate and the trade licence — in the private finance bucket, under a
deterministic key per supplier and kind, signed on read and never public.

Also the one piece of purchase money maths: splitting a VAT-inclusive line total
into the recoverable VAT slice and the net. The VAT stays inside the item's cost
either way (see `Supplier.is_vat_deductible`); the split is recorded for the
reclaim report, not deducted from inventory value.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import object_storage
from app.core.config import settings
from app.core.exceptions import BadGatewayError, BadRequestError, NotFoundError
from app.core.money import money as _money
from app.core.money import unit_cost as _c
from app.models.inventory import (
    InventoryItem,
    Supplier,
    SupplierContact,
    SupplierItem,
)
from app.models.inventory_v2 import Recipe
from app.services.inventory import po_misc_service

logger = logging.getLogger(__name__)

__all__ = [
    "PURCHASE_VAT_RATE",
    "PURCHASABLE_KINDS",
    "VatSplit",
    "split_line_vat",
    "assert_item_purchasable",
    "create_supplier",
    "update_supplier",
    "DOCUMENT_KINDS",
    "DOCUMENT_MAX_BYTES",
    "store_document",
    "remove_document",
    "document_url",
    "active_mapped_items",
    "deactivate_supplier",
    "reactivate_supplier",
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


async def _clean_misc_placement(db: AsyncSession, payload: dict) -> None:
    """Validate the misc-line P&L placement fields present in ``payload``, in
    place. A null list means "none set", the same as an empty one."""
    if "misc_pnl_channels" in payload:
        payload["misc_pnl_channels"] = po_misc_service.clean_pnl_channels(
            payload["misc_pnl_channels"]
        )
    if "misc_pnl_branch_ids" in payload:
        payload["misc_pnl_branch_ids"] = await po_misc_service.clean_pnl_branches(
            db, payload["misc_pnl_branch_ids"]
        )


async def create_supplier(db: AsyncSession, data) -> Supplier:
    payload = data.model_dump(exclude={"contacts"})
    await _clean_misc_placement(db, payload)
    supplier = Supplier(**payload)
    db.add(supplier)
    await db.flush()
    for contact in data.contacts:
        db.add(SupplierContact(supplier_id=supplier.id, **contact.model_dump()))
    await db.flush()
    await db.refresh(supplier)
    return supplier


async def update_supplier(db: AsyncSession, supplier: Supplier, data) -> Supplier:
    payload = data.model_dump(exclude={"contacts"}, exclude_unset=True)
    await _clean_misc_placement(db, payload)
    for key, value in payload.items():
        setattr(supplier, key, value)
    if data.contacts is not None:
        await replace_contacts(db, supplier, data.contacts)
    await db.flush()
    await db.refresh(supplier)
    return supplier


async def active_mapped_items(db: AsyncSession, supplier_id: uuid.UUID) -> list[str]:
    """Names of the still-active items mapped to this supplier, alphabetical.

    "Active" is a property of the mapped *item* — ``SupplierItem`` carries no
    active flag of its own — so a mapping to an item that is itself inactive or
    deleted does not count. This is the set that blocks deactivation.
    """
    return list(
        (
            await db.execute(
                select(InventoryItem.name)
                .join(SupplierItem, SupplierItem.item_id == InventoryItem.id)
                .where(
                    SupplierItem.supplier_id == supplier_id,
                    InventoryItem.is_active.is_(True),
                    InventoryItem.deleted_at.is_(None),
                )
                .order_by(InventoryItem.name)
            )
        )
        .scalars()
        .all()
    )


async def deactivate_supplier(db: AsyncSession, supplier: Supplier) -> Supplier:
    """Move a supplier to the inactive list, once nothing active still maps to it.

    Deactivation is a state flip (``is_active`` false), **not** a delete: the row
    stays visible under the inactive tab, keeps every purchase order it is named
    on (nothing touches ``purchase_orders.supplier_id``), and can be brought back
    with :func:`reactivate_supplier`. It is refused while any *active* inventory
    item is still mapped to the supplier, so a live item never points at a dead
    vendor; a mapping to an already-inactive item does not block it.
    """
    blocking = await active_mapped_items(db, supplier.id)
    if blocking:
        shown = ", ".join(blocking[:5])
        more = f" and {len(blocking) - 5} more" if len(blocking) > 5 else ""
        raise BadRequestError(
            f"{supplier.name} still supplies {len(blocking)} active item"
            f"{'s' if len(blocking) != 1 else ''}: {shown}{more}. Remove those "
            "mappings (or deactivate the items) before deactivating the supplier."
        )
    supplier.is_active = False
    await db.flush()
    await db.refresh(supplier)
    return supplier


async def reactivate_supplier(db: AsyncSession, supplier: Supplier) -> Supplier:
    """Return a supplier to the active list — a plain ``is_active`` flip back."""
    supplier.is_active = True
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


# ─── Registration documents (private GCS bucket) ──────────────────────────────

#: The documents a supplier can carry. Each maps to the ``<kind>_object_key`` /
#: ``<kind>_content_type`` column pair on ``Supplier``.
DOCUMENT_KINDS = ("trn_certificate", "trade_license")

_DOCUMENT_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "application/pdf": ".pdf",
}

#: Same ceiling as a purchase-order invoice: a scan or PDF, not an archive.
DOCUMENT_MAX_BYTES = 10 * 1024 * 1024

_DOCUMENT_LABEL = {
    "trn_certificate": "VAT (TRN) certificate",
    "trade_license": "Trade licence",
}


def _document_key(supplier_id: uuid.UUID, kind: str, ext: str) -> str:
    return f"suppliers/{supplier_id}/{kind}{ext}"


async def store_document(
    db: AsyncSession,
    supplier: Supplier,
    kind: str,
    body: bytes,
    content_type: str,
) -> Supplier:
    """Validate and store one registration document, replacing any previous one.

    A re-upload with a different extension writes a new key, so the old object
    is deleted rather than left orphaned in the bucket.
    """
    label = _DOCUMENT_LABEL[kind]
    ext = _DOCUMENT_EXT.get(content_type)
    if ext is None:
        raise BadRequestError(f"{label} must be a JPEG, PNG, WebP or PDF")
    if not body:
        raise BadRequestError(f"No {label.lower()} file was uploaded")
    if len(body) > DOCUMENT_MAX_BYTES:
        raise BadRequestError(f"{label} file is too large (max 10 MB)")
    key = _document_key(supplier.id, kind, ext)
    previous_key = getattr(supplier, f"{kind}_object_key")
    try:
        # Blocking GCS network I/O — keep it off the event loop.
        await asyncio.to_thread(
            object_storage.upload_object,
            bucket=settings.GCS_INVOICE_BUCKET,
            key=key,
            body=body,
            content_type=content_type,
            cache_control="private, no-store",
        )
    except Exception as exc:
        raise BadGatewayError(f"Failed to store the {label.lower()}") from exc
    if previous_key and previous_key != key:
        await _delete_quietly(previous_key)
    setattr(supplier, f"{kind}_object_key", key)
    setattr(supplier, f"{kind}_content_type", content_type)
    await db.flush()
    await db.refresh(supplier)
    return supplier


async def remove_document(db: AsyncSession, supplier: Supplier, kind: str) -> Supplier:
    """Detach a registration document and delete its object."""
    key = getattr(supplier, f"{kind}_object_key")
    setattr(supplier, f"{kind}_object_key", None)
    setattr(supplier, f"{kind}_content_type", None)
    await db.flush()
    if key:
        await _delete_quietly(key)
    await db.refresh(supplier)
    return supplier


async def document_url(supplier: Supplier, kind: str) -> str:
    """A short-lived signed GET URL for one document, or ``NotFoundError``."""
    key = getattr(supplier, f"{kind}_object_key")
    if not key:
        raise NotFoundError(f"This supplier has no {_DOCUMENT_LABEL[kind].lower()}")
    # Signing goes through the IAM signBlob API — network I/O, off the loop.
    url = await asyncio.to_thread(
        object_storage.signed_url, bucket=settings.GCS_INVOICE_BUCKET, key=key
    )
    if url is None:
        raise BadGatewayError("Could not sign a download link for the document")
    return url


async def _delete_quietly(key: str) -> None:
    """Delete an object we no longer point at. The row is already right; a failed
    delete only leaves an unreferenced object in a private bucket, so log it
    rather than fail the request."""
    try:
        await asyncio.to_thread(
            object_storage.delete_object, bucket=settings.GCS_INVOICE_BUCKET, key=key
        )
    except Exception:
        logger.warning("could not delete supplier document %s", key, exc_info=True)
