"""Resolve an external system's item name/id to a catalogue product.

The read/write layer over `external_item_map` (the generalised sibling of the
GrubOps map). `resolve_product` answers "what MM product does this external item
mean?" from **approved** rows only — an unapproved row is a guess and must not
silently attach, exactly as the GrubOps ingest treats its map. `record_proposal`
is how the queue fills: when promotion meets a name, it records it (with whatever
a name match guessed) as an unapproved row for a human to approve or correct; it
never overwrites a row that already exists, so a human's edit and an approval both
stand.

Keyed on a normalised `external_ref` (lower-cased, trimmed name) so trivial
spelling/spacing differences collapse; the verbatim name is kept in
`external_name` for the review screen. Per the transaction convention nothing here
commits — the caller's request/sweep does.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.external_item_map import (
    KIND_PRODUCT,
    METHOD_EXACT,
    METHOD_FUZZY,
    ExternalItemMap,
)
from app.models.product import Product


def normalize_ref(name: str | None) -> str | None:
    """The match key for a free-text external name: trimmed, lower-cased."""
    if not name or not name.strip():
        return None
    return " ".join(name.strip().lower().split())


async def resolve_product(db: AsyncSession, system: str, name: str | None) -> tuple:
    """The (product_id, sku) an approved map row assigns this external name, or
    (None, "") if none. Approved product rows only — a guess never attaches."""
    ref = normalize_ref(name)
    if ref is None:
        return None, ""
    row = (
        await db.execute(
            select(Product.id, Product.sku)
            .join(ExternalItemMap, ExternalItemMap.product_id == Product.id)
            .where(
                ExternalItemMap.system == system,
                ExternalItemMap.external_ref == ref,
                ExternalItemMap.mm_kind == KIND_PRODUCT,
                ExternalItemMap.approved.is_(True),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        return None, ""
    return row[0], row[1] or ""


async def record_proposal(
    db: AsyncSession,
    system: str,
    name: str | None,
    *,
    guess_product_id=None,
) -> None:
    """Record a first sighting of an external name as an *unapproved* proposal for
    review — carrying whatever a name match guessed (or none). Idempotent and
    never destructive: `ON CONFLICT DO NOTHING`, so an approved row or an operator's
    edit is left exactly as it is, and only a genuinely new name inserts a row."""
    ref = normalize_ref(name)
    if ref is None:
        return
    await db.execute(
        pg_insert(ExternalItemMap)
        .values(
            system=system,
            external_ref=ref,
            external_name=(name or "").strip()[:255] or None,
            mm_kind=KIND_PRODUCT,
            product_id=guess_product_id,
            approved=False,
            match_method=METHOD_EXACT if guess_product_id is not None else METHOD_FUZZY,
            match_score=Decimal("100.00") if guess_product_id is not None else None,
        )
        .on_conflict_do_nothing(constraint="uq_external_item_map_ref")
    )
