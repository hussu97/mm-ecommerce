"""Append-only versioning for inventory transfer templates.

A transfer template is a saved pick-list a branch transfers from. It is versioned
exactly the way a shift-report template is (see ``report_service``): a template is
identified by its ``(source_branch_id, name)`` lineage, editing it inserts a new
row at the next ``version_number`` rather than mutating, and "current" is the
highest ``version_number`` per lineage. Keeping the old revisions means a transfer
order can stamp an immutable snapshot of the template it was raised from, so its
provenance survives a later edit — the same reason a report snapshots its template.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.branch import Branch
from app.models.inventory import InventoryItem
from app.models.operations import (
    InventoryTransferTemplate,
    InventoryTransferTemplateItem,
)
from app.services.inventory import source_event_service

__all__ = [
    "latest_active_templates",
    "upsert_template",
    "deactivate_template",
    "snapshot_template",
]


def latest_active_templates(
    templates: list[InventoryTransferTemplate],
) -> list[InventoryTransferTemplate]:
    """Return the one current template per ``(source_branch_id, name)`` lineage.

    Revision rows stay visible to the admin, but an older active revision must
    never silently return to the register after an operator deactivates the newest
    one. Selecting the newest row *before* considering ``is_active`` gives that
    explicit deactivation its intended meaning — mirrors
    ``report_service.latest_active_templates``.
    """
    newest: dict[tuple[uuid.UUID, str], InventoryTransferTemplate] = {}
    for template in templates:
        key = (template.source_branch_id, template.name)
        current = newest.get(key)
        if current is None or (int(template.version_number or 0), str(template.id)) > (
            int(current.version_number or 0),
            str(current.id),
        ):
            newest[key] = template
    return sorted(
        (template for template in newest.values() if template.is_active),
        key=lambda template: (int(template.display_order or 0), template.name),
    )


async def _next_template_version(
    db: AsyncSession, *, source_branch_id: uuid.UUID, name: str
) -> int:
    """Allocate the next revision under the branch inventory transaction lock."""
    current = await db.scalar(
        select(func.max(InventoryTransferTemplate.version_number)).where(
            InventoryTransferTemplate.source_branch_id == source_branch_id,
            InventoryTransferTemplate.name == name,
        )
    )
    return int(current or 0) + 1


async def _load_template(
    db: AsyncSession, template_id: uuid.UUID
) -> InventoryTransferTemplate:
    template = (
        (
            await db.execute(
                select(InventoryTransferTemplate)
                .where(InventoryTransferTemplate.id == template_id)
                .options(selectinload(InventoryTransferTemplate.items))
            )
        )
        .scalars()
        .unique()
        .one()
    )
    return template


async def upsert_template(db: AsyncSession, *, payload) -> InventoryTransferTemplate:
    """Create the next version of a ``(source_branch_id, name)`` lineage.

    Always inserts a new template row — never mutates an existing one — copying the
    payload's items onto it. Both the admin's create and its edit route here, so an
    edit is simply the next revision, exactly like ``report_service.upsert_template``.
    """
    if await db.get(Branch, payload.source_branch_id) is None:
        raise NotFoundError("Source branch not found")
    if (
        payload.destination_branch_id is not None
        and await db.get(Branch, payload.destination_branch_id) is None
    ):
        raise NotFoundError("Destination branch not found")

    # Serializes adjacent revisions of this lineage. The unique constraint remains
    # the database backstop for an importer or future writer bypassing this service.
    await source_event_service.lock_branch_inventory(db, payload.source_branch_id)
    next_version = await _next_template_version(
        db, source_branch_id=payload.source_branch_id, name=payload.name
    )

    template = InventoryTransferTemplate(
        source_branch_id=payload.source_branch_id,
        destination_branch_id=payload.destination_branch_id,
        name=payload.name,
        is_active=payload.is_active,
        display_order=payload.display_order,
        version_number=next_version,
    )
    db.add(template)
    await db.flush()

    seen: set[uuid.UUID] = set()
    for index, item_data in enumerate(payload.items):
        if item_data.item_id in seen:
            raise BadRequestError(
                f"Inventory item {item_data.item_id} appears more than once"
            )
        seen.add(item_data.item_id)
        if await db.get(InventoryItem, item_data.item_id) is None:
            raise BadRequestError(f"Inventory item {item_data.item_id} not found")
        db.add(
            InventoryTransferTemplateItem(
                template_id=template.id,
                item_id=item_data.item_id,
                display_order=item_data.display_order or index,
            )
        )
    await db.flush()
    return await _load_template(db, template.id)


async def deactivate_template(
    db: AsyncSession, *, template: InventoryTransferTemplate
) -> InventoryTransferTemplate:
    """Deactivate the current revision, refusing to touch an older one.

    Deactivating a superseded revision would be meaningless (it is already hidden
    from the register by ``latest_active_templates``) and would let an even older
    active row surface, so only the latest revision of a lineage may be
    deactivated — mirrors ``report_service.deactivate_template``.
    """
    await source_event_service.lock_branch_inventory(db, template.source_branch_id)
    templates = list(
        (
            await db.execute(
                select(InventoryTransferTemplate)
                .where(
                    InventoryTransferTemplate.source_branch_id
                    == template.source_branch_id,
                    InventoryTransferTemplate.name == template.name,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    latest_revision = max(
        templates,
        key=lambda row: (int(row.version_number or 0), str(row.id)),
    )
    if latest_revision.id != template.id:
        raise BadRequestError(
            "Only the latest transfer-template version can be deactivated"
        )
    if template.is_active:
        template.is_active = False
        await db.flush()
    return template


async def snapshot_template(
    db: AsyncSession, template: InventoryTransferTemplate
) -> dict[str, Any]:
    """The immutable record of a template as it stood when a transfer was raised.

    Stamped onto ``TransferOrder.template_snapshot`` so the order's provenance —
    which template, which version, what it held — survives a later edit or
    deactivation of the live template. Downstream reads this, never the live row.
    The item name/sku are captured here (one lookup) so the snapshot still reads
    even after an item is renamed or removed.
    """
    ids = {item.item_id for item in template.items}
    lookup: dict[uuid.UUID, InventoryItem] = {}
    if ids:
        rows = (
            (await db.execute(select(InventoryItem).where(InventoryItem.id.in_(ids))))
            .scalars()
            .all()
        )
        lookup = {row.id: row for row in rows}

    def item_snapshot(item: InventoryTransferTemplateItem) -> dict[str, Any]:
        row = lookup.get(item.item_id)
        return {
            "item_id": str(item.item_id),
            "display_order": int(item.display_order or 0),
            "item_name": row.name if row is not None else None,
            "item_sku": row.sku if row is not None else None,
        }

    return {
        "name": template.name,
        "version_number": template.version_number,
        "source_branch_id": str(template.source_branch_id),
        "destination_branch_id": str(template.destination_branch_id)
        if template.destination_branch_id is not None
        else None,
        "items": [
            item_snapshot(item)
            for item in sorted(
                template.items, key=lambda row: int(row.display_order or 0)
            )
        ],
    }
