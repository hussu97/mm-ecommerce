"""
Transfer orders, production, spot checks, reservations, notification rules,
and the live branches dashboard.

These close the last verified gaps against the Foodics audit — see
docs/integrators-and-aggregators.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.money import money
from app.core.permissions import ensure, require
from app.models import (
    Branch,
    Device,
    InventoryCategory,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransferTemplate,
    NotificationRule,
    Order,
    PosOrderStatusEnum,
    PosTable,
    Section,
    TableStatusEnum,
    Till,
    TillStatusEnum,
    Transfer,
    TransferKindEnum,
    TransferOrder,
    TransferStatusEnum,
    Warehouse,
)
from app.models.base import utcnow
from app.models.user import User
from app.schemas.inventory import (
    TransferTemplateItemInput,
    TransferTemplateItemResponse,
    TransferTemplateResponse,
    TransferTemplateUpsert,
)
from app.schemas.transfers import (
    TransferLineResponse,
    TransferOrderAdjustmentEntry,
    TransferOrderCreate,
    TransferOrderReport,
    TransferOrderReportChild,
    TransferOrderResponse,
    TransferOrderTotalLine,
    TransferReceive,
    TransferResponse,
    TransferSend,
)
from app.services import crud_service, email_service, push_service
from app.services.inventory import (
    access_service,
    inventory_service,
    transfer_service,
    transfer_template_service,
)
from app.services.pos import business_day_service

from .pos_config import build_crud_router


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


async def _category_map(
    db: AsyncSession, item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, InventoryCategory]:
    """The categories of the given items, keyed by category id, in one query.

    Mirrors ``report_service._category_map``: reading ``item.category`` lazily
    under asyncio raises MissingGreenlet, so the serialisers resolve categories in
    this one explicit query and read the name/display_order off the result.
    """
    if not item_ids:
        return {}
    category_ids = (
        (
            await db.execute(
                select(InventoryItem.category_id)
                .where(
                    InventoryItem.id.in_(item_ids),
                    InventoryItem.category_id.is_not(None),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if not category_ids:
        return {}
    categories = (
        (
            await db.execute(
                select(InventoryCategory).where(InventoryCategory.id.in_(category_ids))
            )
        )
        .scalars()
        .all()
    )
    return {category.id: category for category in categories}


# ─── Transfer orders ──────────────────────────────────────────────────────────

transfer_orders_router = APIRouter()


async def _backfill_lines(
    db: AsyncSession,
    line_payloads: list[TransferLineResponse],
    lines: list,
    *,
    lookup: dict | None = None,
    categories: dict | None = None,
) -> None:
    """Fill item name/sku/category onto serialised child lines from the DB."""
    if lookup is None or categories is None:
        ids = {line.item_id for line in lines}
        if not ids:
            return
        rows = (
            (await db.execute(select(InventoryItem).where(InventoryItem.id.in_(ids))))
            .scalars()
            .all()
        )
        lookup = {r.id: r for r in rows}
        categories = await _category_map(db, list(ids))
    for payload, line in zip(line_payloads, lines):
        item = lookup.get(line.item_id)
        if not item:
            continue
        payload.item_name = item.name
        payload.item_sku = item.sku
        category = categories.get(item.category_id) if item.category_id else None
        if category is not None:
            payload.category_name = category.name
            payload.category_order = int(category.display_order or 0)


async def _serialise_child(db: AsyncSession, transfer: Transfer) -> TransferResponse:
    payload = TransferResponse.model_validate(transfer)
    await _backfill_lines(db, payload.items, transfer.items)
    return payload


async def _serialise_order(
    db: AsyncSession, order: TransferOrder
) -> TransferOrderResponse:
    payload = TransferOrderResponse.model_validate(order)
    item_ids = {line.item_id for child in order.children for line in child.items}
    lookup: dict = {}
    categories: dict = {}
    if item_ids:
        rows = (
            (
                await db.execute(
                    select(InventoryItem).where(InventoryItem.id.in_(item_ids))
                )
            )
            .scalars()
            .all()
        )
        lookup = {r.id: r for r in rows}
        categories = await _category_map(db, list(item_ids))

    totals: dict[uuid.UUID, TransferOrderTotalLine] = {}
    for child_payload, child in zip(payload.children, order.children):
        await _backfill_lines(
            db, child_payload.items, child.items, lookup=lookup, categories=categories
        )
        for line in child.items:
            qty = Decimal(str(line.quantity))
            total = totals.get(line.item_id)
            if total is None:
                item = lookup.get(line.item_id)
                category = (
                    categories.get(item.category_id)
                    if item and item.category_id
                    else None
                )
                totals[line.item_id] = TransferOrderTotalLine(
                    item_id=line.item_id,
                    item_name=item.name if item else None,
                    item_sku=item.sku if item else None,
                    unit=line.unit,
                    total_quantity=qty,
                    category_name=category.name if category else None,
                    category_order=(
                        int(category.display_order or 0) if category else None
                    ),
                )
            else:
                total.total_quantity += qty
    payload.total_by_item = sorted(
        totals.values(),
        key=lambda r: (
            r.category_order if r.category_order is not None else 9999,
            r.item_name or "",
        ),
    )
    return payload


def _is_super(user: User) -> bool:
    return bool(user.is_admin or (user.role and user.role.is_super_admin))


async def _assert_order_access(
    db: AsyncSession, user: User, order: TransferOrder
) -> None:
    """A user may see an order they source from or that ships to one of their
    branches."""
    if _is_super(user):
        return
    allowed = set(access_service.branch_ids_for(user))
    destinations = {child.branch_id for child in order.children}
    if order.source_branch_id in allowed or (destinations & allowed):
        return
    await access_service.assert_branch_access(db, user, order.source_branch_id)


async def _build_report(db: AsyncSession, order: TransferOrder) -> TransferOrderReport:
    branch_ids = {order.source_branch_id} | {c.branch_id for c in order.children}
    branches = (
        (await db.execute(select(Branch).where(Branch.id.in_(branch_ids))))
        .scalars()
        .all()
    )
    names = {b.id: b.name for b in branches}

    tx_ids = {
        tid
        for child in order.children
        for tid in (child.sent_transaction_id, child.received_transaction_id)
        if tid is not None
    }
    tx_totals: dict[uuid.UUID, Decimal] = {}
    if tx_ids:
        rows = (
            await db.execute(
                select(InventoryTransaction.id, InventoryTransaction.total_cost).where(
                    InventoryTransaction.id.in_(tx_ids)
                )
            )
        ).all()
        tx_totals = {row[0]: Decimal(str(row[1] or 0)) for row in rows}

    children = [
        TransferOrderReportChild(
            transfer_id=child.id,
            reference=child.reference,
            destination_branch_id=child.branch_id,
            destination_branch_name=names.get(child.branch_id),
            status=child.status,
            item_count=len(child.items),
            total_sent=sum(
                (Decimal(str(line.sent_quantity)) for line in child.items),
                Decimal("0"),
            ),
            total_received=sum(
                (Decimal(str(line.received_quantity)) for line in child.items),
                Decimal("0"),
            ),
            sent_value=tx_totals.get(child.sent_transaction_id, Decimal("0")),
            received_value=tx_totals.get(child.received_transaction_id, Decimal("0")),
            # A sending variance can only exist once the leg has shipped; before
            # that sent_quantity is still 0 against every requested line.
            has_sending_variance=child.sent_transaction_id is not None
            and any(
                Decimal(str(line.sent_quantity)) != Decimal(str(line.quantity))
                for line in child.items
            ),
        )
        for child in order.children
    ]

    adjustments: list[TransferOrderAdjustmentEntry] = []
    adjustment_total = Decimal("0")
    if order.adjustment_group_id is not None:
        adj_txs = (
            (
                await db.execute(
                    select(InventoryTransaction)
                    .where(
                        InventoryTransaction.correction_group_id
                        == order.adjustment_group_id
                    )
                    .options(selectinload(InventoryTransaction.items))
                )
            )
            .scalars()
            .unique()
            .all()
        )
        item_ids = {it.item_id for tx in adj_txs for it in tx.items}
        item_names: dict[uuid.UUID, str] = {}
        if item_ids:
            rows = (
                await db.execute(
                    select(InventoryItem.id, InventoryItem.name).where(
                        InventoryItem.id.in_(item_ids)
                    )
                )
            ).all()
            item_names = {row[0]: row[1] for row in rows}
        for tx in adj_txs:
            for it in tx.items:
                qty = Decimal(str(it.quantity))
                cost = Decimal(str(it.unit_cost or 0))
                value = qty * cost
                adjustment_total += value
                adjustments.append(
                    TransferOrderAdjustmentEntry(
                        transaction_id=tx.id,
                        transaction_reference=tx.reference,
                        item_id=it.item_id,
                        item_name=item_names.get(it.item_id),
                        quantity=qty,
                        unit_cost=cost,
                        value=value,
                    )
                )

    return TransferOrderReport(
        id=order.id,
        reference=order.reference,
        status=order.status,
        kind=order.kind,
        source_branch_id=order.source_branch_id,
        source_branch_name=names.get(order.source_branch_id),
        business_date=order.business_date,
        created_at=order.created_at,
        children=children,
        adjustments=adjustments,
        adjustment_total=adjustment_total,
    )


@transfer_orders_router.get("", response_model=list[TransferOrderResponse])
async def list_transfer_orders(
    source_branch_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    # The admin log fetches history for client-side paging, so allow up to the
    # console's max page size (2000) rather than capping at 1000.
    limit: int = Query(100, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    stmt = select(TransferOrder)
    if source_branch_id:
        await access_service.assert_branch_access(db, user, source_branch_id)
        stmt = stmt.where(TransferOrder.source_branch_id == source_branch_id)
    elif not _is_super(user):
        allowed = access_service.branch_ids_for(user)
        stmt = stmt.where(
            or_(
                TransferOrder.source_branch_id.in_(allowed),
                TransferOrder.id.in_(
                    select(Transfer.transfer_order_id).where(
                        Transfer.branch_id.in_(allowed)
                    )
                ),
            )
        )
    if status_filter:
        stmt = stmt.where(TransferOrder.status == status_filter)
    stmt = stmt.order_by(TransferOrder.created_at.desc()).limit(limit)
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_order(db, o) for o in orders]


@transfer_orders_router.get("/{order_id}", response_model=TransferOrderResponse)
async def get_transfer_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """One transfer order with its per-branch children — the ledger's transfer
    source links here and the console's detail page reads it."""
    order = await transfer_service.load_transfer_order(db, order_id)
    await _assert_order_access(db, user, order)
    return await _serialise_order(db, order)


@transfer_orders_router.post(
    "", response_model=TransferOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_transfer_order(
    data: TransferOrderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Raise a transfer order from one source branch, fanning out to many. Sending
    more of an item than the source holds requires ``override`` on that item, which
    also needs the adjustments permission (it writes stock off)."""
    if any(item.override for item in data.items):
        ensure(
            user,
            "inventory.adjustments.manage",
            message="Overriding stock on hand needs the adjustments permission",
        )
    source = await crud_service.get_or_404(db, Branch, data.source_branch_id)
    await access_service.assert_branch_access(db, user, data.source_branch_id)
    if data.source_warehouse_id:
        await inventory_service.assert_warehouse_for_branch(
            db, data.source_warehouse_id, data.source_branch_id
        )
    destination_ids = {
        allocation.branch_id for item in data.items for allocation in item.allocations
    }
    for branch_id in destination_ids:
        await crud_service.get_or_404(db, Branch, branch_id)

    order = await transfer_service.create_transfer_order(
        db,
        source_branch=source,
        user=user,
        items=data.items,
        kind=data.kind,
        notes=data.notes,
        required_date=data.required_date,
        template_id=data.template_id,
        client_request_id=data.client_request_id,
    )
    return await _serialise_order(db, order)


@transfer_orders_router.get("/{order_id}/report", response_model=TransferOrderReport)
async def get_transfer_order_report(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """The parent report: per-branch children with statuses and movement values,
    plus the mini stock-adjustment report from any override at create time."""
    order = await transfer_service.load_transfer_order(db, order_id)
    await _assert_order_access(db, user, order)
    return await _build_report(db, order)


# ─── Transfer templates (admin) ───────────────────────────────────────────────

transfer_templates_router = APIRouter()


async def _serialise_template(
    db: AsyncSession, template: InventoryTransferTemplate
) -> TransferTemplateResponse:
    payload = TransferTemplateResponse.model_validate(template)
    ids = {line.item_id for line in template.items}
    if ids:
        rows = (
            (await db.execute(select(InventoryItem).where(InventoryItem.id.in_(ids))))
            .scalars()
            .all()
        )
        lookup = {r.id: r for r in rows}
        categories = await _category_map(db, [r.id for r in rows])
        visible = []
        for line in payload.items:
            item = lookup.get(line.item_id)
            # Drop lines whose item has since been deactivated or deleted — a
            # stale template must not offer an item nobody can transfer.
            if item is None or item.deleted_at is not None or not item.is_active:
                continue
            line.item_name = item.name
            line.item_sku = item.sku
            category = categories.get(item.category_id) if item.category_id else None
            if category is not None:
                line.category_name = category.name
                line.category_order = int(category.display_order or 0)
            visible.append(line)
        payload.items = visible
    return payload


async def _load_template(
    db: AsyncSession, template_id: uuid.UUID
) -> InventoryTransferTemplate:
    # Eager-load items: a plain db.get returns the just-flushed row from the
    # identity map without its collection, and serialising it would then trigger
    # an async lazy load during Pydantic's synchronous attribute access (a
    # MissingGreenlet 500). Load it the way load_transfer_order does.
    template = (
        (
            await db.execute(
                select(InventoryTransferTemplate)
                .where(InventoryTransferTemplate.id == template_id)
                .options(selectinload(InventoryTransferTemplate.items))
                # populate_existing so an update's reload overwrites the cached
                # items collection with the fresh list rather than the stale one
                # from before the delete/re-add.
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if template is None:
        raise NotFoundError("Transfer template not found")
    return template


@transfer_templates_router.get("", response_model=list[TransferTemplateResponse])
async def list_transfer_templates(
    source_branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    stmt = select(InventoryTransferTemplate)
    if source_branch_id:
        await access_service.assert_branch_access(db, user, source_branch_id)
        stmt = stmt.where(
            InventoryTransferTemplate.source_branch_id == source_branch_id
        )
    elif not (user.is_admin or (user.role and user.role.is_super_admin)):
        stmt = stmt.where(
            InventoryTransferTemplate.source_branch_id.in_(
                access_service.branch_ids_for(user)
            )
        )
    # Newest revision of each lineage first, so the admin can render the version
    # history and mark Current vs Superseded — mirrors the report-template list.
    stmt = stmt.order_by(
        InventoryTransferTemplate.source_branch_id,
        InventoryTransferTemplate.name,
        InventoryTransferTemplate.version_number.desc(),
    )
    templates = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_template(db, t) for t in templates]


@transfer_templates_router.post(
    "", response_model=TransferTemplateResponse, status_code=status.HTTP_201_CREATED
)
async def create_transfer_template(
    data: TransferTemplateUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    await access_service.assert_branch_access(db, user, data.source_branch_id)
    # Templates are append-only revisions: this inserts v1 (or the next version if
    # the lineage already exists), never mutating an existing row.
    template = await transfer_template_service.upsert_template(db, payload=data)
    return await _serialise_template(db, template)


@transfer_templates_router.put(
    "/{template_id}", response_model=TransferTemplateResponse
)
async def update_transfer_template(
    template_id: uuid.UUID,
    data: TransferTemplateUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    # Load the target for its 404 and branch-access check, then append a new
    # version for its (source_branch_id, name) lineage — the old revision stays as
    # history. Editing is the next version, exactly like a report template.
    template = await _load_template(db, template_id)
    await access_service.assert_branch_access(db, user, template.source_branch_id)
    await access_service.assert_branch_access(db, user, data.source_branch_id)
    created = await transfer_template_service.upsert_template(db, payload=data)
    return await _serialise_template(db, created)


@transfer_templates_router.post(
    "/{template_id}/deactivate", response_model=TransferTemplateResponse
)
async def deactivate_transfer_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Retire a template without deleting it — only the latest revision of a
    lineage may be deactivated, so an older active revision cannot resurface on the
    register. Orders raised from it keep their snapshot regardless."""
    template = await _load_template(db, template_id)
    await access_service.assert_branch_access(db, user, template.source_branch_id)
    template = await transfer_template_service.deactivate_template(
        db, template=template
    )
    return await _serialise_template(db, template)


# ─── Transfers on the till (POS) ──────────────────────────────────────────────

pos_transfers_router = APIRouter()


class PosReturnLineInput(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    unit: Literal["storage", "ingredient"] = "storage"
    #: Why the goods are going back — required on a return.
    variance_reason: str = Field(min_length=1, max_length=255)


class PosReturnCreate(BaseModel):
    source_branch_id: uuid.UUID
    notes: str | None = None
    client_request_id: str | None = Field(None, max_length=64)
    lines: list[PosReturnLineInput] = Field(min_length=1)


class TransferBranchResponse(BaseModel):
    id: uuid.UUID
    name: str
    reference: str
    uses_pos: bool


@pos_transfers_router.get("/branches", response_model=list[TransferBranchResponse])
async def pos_transfer_branches(
    exclude_branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """The branches a transfer can be sent to — every active branch, so a cashier
    can pick a destination. Excludes the current branch when it is named."""
    stmt = select(Branch).where(Branch.is_active.is_(True))
    if exclude_branch_id:
        stmt = stmt.where(Branch.id != exclude_branch_id)
    stmt = stmt.order_by(Branch.display_order, Branch.name)
    branches = list((await db.execute(stmt)).scalars().all())
    return [
        TransferBranchResponse(
            id=b.id,
            name=b.name,
            reference=b.reference,
            uses_pos=b.uses_pos,
        )
        for b in branches
    ]


class PosOnHandResponse(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal
    item_name: str | None = None
    item_sku: str | None = None
    ingredient_unit: str | None = None
    #: The item's inventory category, so the returns picker can group by it. Both
    #: null for an uncategorised item (sort last).
    category_name: str | None = None
    category_order: int | None = None


@pos_transfers_router.get("/on-hand", response_model=list[PosOnHandResponse])
async def pos_on_hand(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """What the branch holds, to build a return from — gated on the transfers
    permission (not the broader reports one), so the returns picker works for a
    user who can only transfer. Aggregated per item across warehouses, so a branch
    with more than one stock location shows each item once."""
    await access_service.assert_branch_access(db, user, branch_id)
    stmt = (
        select(InventoryLevel, InventoryItem)
        .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
        .join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id)
        .where(
            Warehouse.branch_id == branch_id,
            InventoryItem.deleted_at.is_(None),
            InventoryItem.is_active.is_(True),
        )
        .order_by(InventoryItem.name)
    )
    agg: dict[uuid.UUID, PosOnHandResponse] = {}
    items: dict[uuid.UUID, InventoryItem] = {}
    for level, item in (await db.execute(stmt)).all():
        quantity = Decimal(str(level.quantity))
        existing = agg.get(item.id)
        if existing is not None:
            existing.quantity += quantity
        else:
            items[item.id] = item
            agg[item.id] = PosOnHandResponse(
                item_id=item.id,
                quantity=quantity,
                item_name=item.name,
                item_sku=item.sku,
                ingredient_unit=item.ingredient_unit,
            )
    categories = await _category_map(db, list(items))
    for item_id, row in agg.items():
        item = items[item_id]
        category = categories.get(item.category_id) if item.category_id else None
        if category is not None:
            row.category_name = category.name
            row.category_order = int(category.display_order or 0)
    return list(agg.values())


@pos_transfers_router.get(
    "/transfer-templates", response_model=list[TransferTemplateResponse]
)
async def pos_transfer_templates(
    source_branch_id: uuid.UUID,
    destination_branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """The active templates the current branch transfers from, to seed the create
    screen. Only the *current* revision of each lineage is offered — the register
    must never see a superseded version — so every revision is fetched and reduced
    by ``latest_active_templates`` (newest-per-lineage, then active). A template
    pinned to one destination is offered only for that one."""
    await access_service.assert_branch_access(db, user, source_branch_id)
    stmt = (
        select(InventoryTransferTemplate)
        .where(InventoryTransferTemplate.source_branch_id == source_branch_id)
        .options(selectinload(InventoryTransferTemplate.items))
    )
    revisions = list((await db.execute(stmt)).scalars().unique().all())
    templates = transfer_template_service.latest_active_templates(revisions)
    if destination_branch_id:
        templates = [
            template
            for template in templates
            if template.destination_branch_id in (None, destination_branch_id)
        ]
    return [await _serialise_template(db, t) for t in templates]


@pos_transfers_router.get("/transfers/incoming", response_model=list[TransferResponse])
async def pos_incoming_transfers(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.receive")),
):
    """Transfers and returns sent to this branch and not yet booked in — the list
    the receiving tills work from."""
    await access_service.assert_branch_access(db, user, branch_id)
    stmt = (
        select(Transfer)
        .where(
            Transfer.branch_id == branch_id,
            Transfer.sent_transaction_id.isnot(None),
            Transfer.received_transaction_id.is_(None),
        )
        .options(selectinload(Transfer.items))
        .order_by(Transfer.created_at.desc())
    )
    transfers = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_child(db, t) for t in transfers]


@pos_transfers_router.get("/transfers/outgoing", response_model=list[TransferResponse])
async def pos_outgoing_transfers(
    source_branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.send")),
):
    """Pending transfers this branch is to pack and send — admin created them, so
    they wait for the source till to mark each one sent. Distinct from
    ``incoming``, which is the destination's receive list."""
    await access_service.assert_branch_access(db, user, source_branch_id)
    stmt = (
        select(Transfer)
        .where(
            Transfer.source_branch_id == source_branch_id,
            Transfer.sent_transaction_id.is_(None),
            Transfer.status == TransferStatusEnum.PENDING.value,
        )
        .options(selectinload(Transfer.items))
        .order_by(Transfer.created_at.desc())
    )
    transfers = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_child(db, t) for t in transfers]


@pos_transfers_router.get("/transfers/completed", response_model=list[TransferResponse])
async def pos_completed_transfers(
    branch_id: uuid.UUID,
    limit: int = Query(200, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """This branch's finished transfers, both directions, for the completed-transfer
    report: legs it has *sent* (source, shipped) and legs it has *received*
    (destination, booked in). The client tells the two apart by comparing
    ``source_branch_id``/``branch_id`` to its own branch. Gated on the broad
    ``manage`` permission — same as the report link that opens it."""
    await access_service.assert_branch_access(db, user, branch_id)
    stmt = (
        select(Transfer)
        .where(
            or_(
                and_(
                    Transfer.source_branch_id == branch_id,
                    Transfer.sent_transaction_id.isnot(None),
                ),
                and_(
                    Transfer.branch_id == branch_id,
                    Transfer.received_transaction_id.isnot(None),
                ),
            )
        )
        .options(selectinload(Transfer.items))
        .order_by(Transfer.updated_at.desc())
        .limit(limit)
    )
    transfers = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_child(db, t) for t in transfers]


@pos_transfers_router.post(
    "/transfers/claim-autoprint", response_model=list[TransferResponse]
)
async def pos_claim_autoprint_transfers(
    source_branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("pos.till.manage")),
):
    """Claim, once, the pending transfers whose order date is today, for the
    source till to auto-print when it opens. Gated on the till permission because
    the trigger is opening a till. The claim is atomic: the first till to open on
    the date stamps ``auto_printed_at`` and gets the list; a later till the same
    day gets nothing, so nothing reprints. A future-dated order is claimed on its
    date, not before. The manual Print button is independent of all this."""
    await access_service.assert_branch_access(db, user, source_branch_id)
    source = await crud_service.get_or_404(db, Branch, source_branch_id)
    business_date = await business_day_service.current_business_date(db, source)
    today = date.fromisoformat(business_date)
    # Orders due today: an explicit required_date of today, or (none set) raised
    # on today's business date.
    due_orders = select(TransferOrder.id).where(
        or_(
            TransferOrder.required_date == today,
            and_(
                TransferOrder.required_date.is_(None),
                TransferOrder.business_date == business_date,
            ),
        )
    )
    claimed_ids = list(
        (
            await db.execute(
                update(Transfer)
                .where(
                    Transfer.source_branch_id == source_branch_id,
                    Transfer.status == TransferStatusEnum.PENDING.value,
                    Transfer.sent_transaction_id.is_(None),
                    Transfer.auto_printed_at.is_(None),
                    Transfer.transfer_order_id.in_(due_orders),
                )
                .values(auto_printed_at=utcnow())
                .returning(Transfer.id)
            )
        )
        .scalars()
        .all()
    )
    if not claimed_ids:
        return []
    rows = list(
        (
            await db.execute(
                select(Transfer)
                .where(Transfer.id.in_(claimed_ids))
                .options(selectinload(Transfer.items))
                .order_by(Transfer.created_at.desc())
            )
        )
        .scalars()
        .unique()
        .all()
    )
    return [await _serialise_child(db, t) for t in rows]


@pos_transfers_router.post(
    "/transfers/{transfer_id}/send", response_model=TransferResponse
)
async def pos_send_transfer(
    transfer_id: uuid.UUID,
    data: TransferSend | None = None,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.send")),
):
    """Mark a pending transfer sent from the source — its stock leaves now, and the
    destination's tills are told there is one to receive.

    ``data`` optionally carries the actual per-line sent quantities the picker
    keyed in; a line left out (or a bodyless call) ships its requested quantity.
    When any line ships with sent ≠ requested, the office is emailed the variance."""
    sent_map = (
        {line.transfer_line_id: line.sent_quantity for line in data.lines}
        if data and data.lines
        else None
    )
    transfer = await transfer_service.load_transfer(db, transfer_id)
    await access_service.assert_branch_access(db, user, transfer.source_branch_id)
    await transfer_service.mark_transfer_sent(
        db, transfer=transfer, user=user, sent=sent_map
    )
    transfer = await transfer_service.load_transfer(db, transfer_id)
    payload = await _serialise_child(db, transfer)

    destination = await db.get(Branch, transfer.branch_id)
    if destination is not None and getattr(destination, "uses_pos", True):
        await push_service.notify_transfer_created(
            db,
            destination_branch_id=destination.id,
            reference=transfer.reference,
            item_count=len(transfer.items),
            kind=transfer.kind,
        )

    # A sending variance — the till shipped more or fewer of a line than the
    # order requested — is worth telling the office about, once per shipment.
    variance_lines = [
        {
            "item_name": line.item_name or line.item_sku or str(line.item_id),
            "requested": f"{Decimal(str(line.quantity)):f}",
            "sent": f"{Decimal(str(line.sent_quantity)):f}",
            "delta": f"{Decimal(str(line.sent_quantity)) - Decimal(str(line.quantity)):+f}",
        }
        for line in payload.items
        if Decimal(str(line.sent_quantity)) != Decimal(str(line.quantity))
    ]
    if variance_lines:
        order = await db.get(TransferOrder, transfer.transfer_order_id)
        source = await db.get(Branch, transfer.source_branch_id)
        await email_service.send_transfer_sending_variance(
            order_id=str(transfer.transfer_order_id),
            order_reference=order.reference if order else transfer.reference,
            transfer_reference=transfer.reference,
            source_branch_name=source.name if source else "",
            destination_branch_name=destination.name if destination else "",
            business_date=transfer.business_date,
            sent_by=user.display_name or user.email,
            lines=variance_lines,
        )

    return payload


@pos_transfers_router.post(
    "/transfers/{transfer_id}/receive", response_model=TransferResponse
)
async def pos_receive_transfer(
    transfer_id: uuid.UUID,
    data: TransferReceive,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.receive")),
):
    """Book in what arrived — short or over. The difference stays on the record with
    the receiver's reason; no separate movement is posted (the send already moved
    the stock)."""
    transfer = await transfer_service.load_transfer(db, transfer_id)
    await access_service.assert_branch_access(db, user, transfer.branch_id)
    received = {line.transfer_line_id: line.received_quantity for line in data.lines}
    reasons = {
        line.transfer_line_id: line.reason
        for line in data.lines
        if line.reason is not None
    }
    await transfer_service.receive_transfer(
        db,
        transfer=transfer,
        user=user,
        received=received or None,
        reasons=reasons or None,
    )
    return await _serialise_child(
        db, await transfer_service.load_transfer(db, transfer_id)
    )


@pos_transfers_router.post(
    "/returns",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
)
async def pos_create_return(
    data: PosReturnCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.send")),
):
    """Send goods back to this branch's return branch (surplus, expired, damaged).
    A return ships immediately as a single-child order; the return branch receives
    it from its "to receive" list, distinguished by its ``return`` kind.
    """
    await access_service.assert_branch_access(db, user, data.source_branch_id)
    source = await crud_service.get_or_404(db, Branch, data.source_branch_id)
    if source.return_branch_id is None:
        raise BadRequestError(
            "This branch has no return branch configured. Set one in the admin console."
        )
    destination = await crud_service.get_or_404(db, Branch, source.return_branch_id)
    order = await transfer_service.create_return_order(
        db,
        source_branch=source,
        destination_branch=destination,
        user=user,
        lines=data.lines,
        notes=data.notes,
        client_request_id=data.client_request_id,
    )
    if getattr(destination, "uses_pos", True):
        await push_service.notify_transfer_created(
            db,
            destination_branch_id=destination.id,
            reference=order.reference,
            item_count=len(data.lines),
            kind=TransferKindEnum.RETURN.value,
        )
    # A return is a single-child order — hand that child back for the till.
    child = order.children[0]
    return await _serialise_child(
        db, await transfer_service.load_transfer(db, child.id)
    )


# ─── Production ───────────────────────────────────────────────────────────────

production_router = APIRouter()


class ProduceRequest(BaseModel):
    branch_id: uuid.UUID
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    warehouse_id: uuid.UUID | None = None
    notes: str | None = None


class ProduceResponse(BaseModel):
    production_reference: str
    consumption_reference: str | None
    produced_quantity: Decimal
    unit_cost: Decimal
    total_cost: Decimal


@production_router.post("", response_model=ProduceResponse)
async def produce(
    data: ProduceRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.adjustments.manage")),
):
    """
    Produce a batch, consuming its bill of materials in the same step.
    Yield loss is applied to the output, so cost per unit rises with waste
    rather than the loss disappearing.
    """
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    await access_service.assert_branch_access(db, user, data.branch_id)
    production, consumption = await transfer_service.produce(
        db,
        branch=branch,
        user=user,
        item_id=data.item_id,
        quantity=data.quantity,
        warehouse_id=data.warehouse_id,
        notes=data.notes,
    )
    line = production.items[0] if production.items else None
    return ProduceResponse(
        production_reference=production.reference,
        consumption_reference=consumption.reference if consumption else None,
        produced_quantity=Decimal(str(line.quantity)) if line else Decimal("0"),
        unit_cost=Decimal(str(line.unit_cost)) if line else Decimal("0"),
        total_cost=Decimal(str(production.total_cost)),
    )


# ─── Notification rules ───────────────────────────────────────────────────────


class NotificationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    event: str = Field(min_length=1, max_length=60)
    threshold: Decimal | None = None
    branch_ids: list[uuid.UUID] = Field(default_factory=list)
    recipient_user_ids: list[uuid.UUID] = Field(default_factory=list)
    recipient_emails: list[str] = Field(default_factory=list)
    channels: list[Literal["email", "sms", "push"]] = Field(
        default_factory=lambda: ["email"]
    )
    is_active: bool = True


class NotificationRuleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=150)
    event: str | None = Field(None, min_length=1, max_length=60)
    threshold: Decimal | None = None
    branch_ids: list[uuid.UUID] | None = None
    recipient_user_ids: list[uuid.UUID] | None = None
    recipient_emails: list[str] | None = None
    channels: list[Literal["email", "sms", "push"]] | None = None
    is_active: bool | None = None


class NotificationRuleResponse(ORMModel):
    id: uuid.UUID
    name: str
    event: str
    threshold: Decimal | None
    branch_ids: list[uuid.UUID]
    recipient_user_ids: list[uuid.UUID]
    recipient_emails: list[str]
    channels: list[str]
    is_active: bool
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


notification_rules_router = build_crud_router(
    model=NotificationRule,
    create_schema=NotificationRuleCreate,
    update_schema=NotificationRuleUpdate,
    response_schema=NotificationRuleResponse,
    entity_type="notification_rule",
)


# ─── Live branches dashboard ──────────────────────────────────────────────────

dashboard_router = APIRouter()


class BranchLive(BaseModel):
    branch_id: uuid.UUID
    branch_name: str
    reference: str
    active_orders: int
    active_orders_amount: Decimal
    occupied_tables: int
    total_tables: int
    open_tills: int
    total_devices: int
    offline_devices: int
    last_order_at: datetime | None
    last_device_seen_at: datetime | None


class BranchesDashboard(BaseModel):
    active_orders: int
    active_orders_amount: Decimal
    occupied_tables: int
    offline_devices: int
    branches: list[BranchLive]


#: A terminal that has not checked in for this long is treated as offline.
OFFLINE_AFTER_SECONDS = 300


@dashboard_router.get("/branches", response_model=BranchesDashboard)
async def branches_dashboard(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("dashboard.access")),
):
    """
    The "what is happening right now" view: open checks, occupied tables, open
    tills and terminals that have gone quiet.
    """

    branches = await crud_service.list_all(db, Branch, include_inactive=False)
    rows: list[BranchLive] = []
    now = utcnow()

    for branch in branches:
        active = (
            await db.execute(
                select(
                    func.count(Order.id), func.coalesce(func.sum(Order.total), 0)
                ).where(
                    Order.is_pos.is_(True),
                    Order.branch_id == branch.id,
                    Order.pos_status == PosOrderStatusEnum.ACTIVE.value,
                )
            )
        ).one()

        tables = (
            await db.execute(
                select(
                    func.count(PosTable.id),
                    func.count(PosTable.id).filter(
                        PosTable.status == TableStatusEnum.OCCUPIED.value
                    ),
                )
                .select_from(PosTable)
                .join(Section, Section.id == PosTable.section_id)
                .where(
                    Section.branch_id == branch.id,
                    PosTable.deleted_at.is_(None),
                )
            )
        ).one()

        open_tills = int(
            (
                await db.execute(
                    select(func.count())
                    .select_from(Till)
                    .where(
                        Till.branch_id == branch.id,
                        Till.status == TillStatusEnum.OPEN.value,
                    )
                )
            ).scalar_one()
        )

        devices = list(
            (
                await db.execute(
                    select(Device).where(
                        Device.branch_id == branch.id,
                        Device.deleted_at.is_(None),
                        Device.type.in_(["cashier", "sub_cashier"]),
                    )
                )
            )
            .scalars()
            .all()
        )
        offline = sum(
            1
            for d in devices
            if d.last_seen_at is None
            or (now - d.last_seen_at).total_seconds() > OFFLINE_AFTER_SECONDS
        )
        last_seen = max(
            (d.last_seen_at for d in devices if d.last_seen_at), default=None
        )

        last_order = (
            await db.execute(
                select(func.max(Order.opened_at)).where(
                    Order.is_pos.is_(True), Order.branch_id == branch.id
                )
            )
        ).scalar_one_or_none()

        rows.append(
            BranchLive(
                branch_id=branch.id,
                branch_name=branch.name,
                reference=branch.reference,
                active_orders=int(active[0] or 0),
                active_orders_amount=Decimal(str(active[1] or 0)),
                total_tables=int(tables[0] or 0),
                occupied_tables=int(tables[1] or 0),
                open_tills=open_tills,
                total_devices=len(devices),
                offline_devices=offline,
                last_order_at=last_order,
                last_device_seen_at=last_seen,
            )
        )

    return BranchesDashboard(
        active_orders=sum(r.active_orders for r in rows),
        active_orders_amount=sum(
            (r.active_orders_amount for r in rows), start=Decimal("0")
        ),
        occupied_tables=sum(r.occupied_tables for r in rows),
        offline_devices=sum(r.offline_devices for r in rows),
        branches=rows,
    )


# ─── Live terminals dashboard ─────────────────────────────────────────────────


class TerminalLive(BaseModel):
    device_id: uuid.UUID
    name: str
    reference: str
    type: str
    status: str
    branch_id: uuid.UUID
    branch_name: str
    last_seen_at: datetime | None
    is_online: bool
    app_version: str | None
    os_version: str | None
    model_identifier: str | None
    open_till_id: uuid.UUID | None
    open_till_user: str | None
    open_till_opened_at: datetime | None
    active_orders: int
    active_orders_amount: Decimal
    orders_today: int
    sales_today: Decimal


class TerminalsDashboard(BaseModel):
    terminals: int
    online: int
    offline: int
    open_tills: int
    orders_today: int
    sales_today: Decimal
    devices: list[TerminalLive]


@dashboard_router.get("/devices", response_model=TerminalsDashboard)
async def terminals_dashboard(
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("dashboard.access")),
):
    """
    Every POS machine, one row each: is it online, whose shift is on it, and what
    it has taken today.

    The branches dashboard already counts terminals per site, which answers "is
    anything down" but not "which one". A manager standing in a branch with three
    tills needs the name of the one that stopped, and the till it stopped
    mid-shift — so this reports per device rather than per branch.

    Scoped by the branch's own `business_date`, not by `created_at`: a shop that
    trades past midnight has one trading day, and a wall-clock "today" would
    report the late half of a Friday night against Saturday and make every
    terminal look quiet.

    Read behind `dashboard.access` rather than admin rights. The device list in
    `/devices` needs `admin.devices.manage` because it hands out pairing codes;
    nothing here does, so a shift manager can see it without being able to pair
    anything.
    """

    branch_filters = [Branch.is_active.is_(True), Branch.deleted_at.is_(None)]
    if branch_id:
        branch_filters.append(Branch.id == branch_id)
    branches = list(
        (await db.execute(select(Branch).where(*branch_filters))).scalars().all()
    )
    branch_names = {b.id: b.name for b in branches}

    device_filters = [
        Device.branch_id.in_(list(branch_names) or [uuid.uuid4()]),
        Device.deleted_at.is_(None),
        # Kitchen displays and printer bridges are not machines anyone sells on.
        Device.type.in_(["cashier", "sub_cashier"]),
    ]
    devices = list(
        (
            await db.execute(
                select(Device).where(*device_filters).order_by(Device.name.asc())
            )
        )
        .scalars()
        .all()
    )

    # One business date per branch, resolved once: two terminals in the same
    # branch must be measured against the same day or their totals will not sum
    # to the branch's.
    business_dates: dict[uuid.UUID, str] = {}
    for branch in branches:
        business_dates[branch.id] = await business_day_service.current_business_date(
            db, branch
        )

    now = utcnow()
    rows: list[TerminalLive] = []

    for device in devices:
        active = (
            await db.execute(
                select(
                    func.count(Order.id), func.coalesce(func.sum(Order.total), 0)
                ).where(
                    Order.is_pos.is_(True),
                    Order.device_id == device.id,
                    Order.pos_status == PosOrderStatusEnum.ACTIVE.value,
                )
            )
        ).one()

        today = (
            await db.execute(
                select(
                    func.count(Order.id), func.coalesce(func.sum(Order.total), 0)
                ).where(
                    Order.is_pos.is_(True),
                    Order.device_id == device.id,
                    Order.business_date == business_dates.get(device.branch_id),
                    Order.pos_status == PosOrderStatusEnum.CLOSED.value,
                )
            )
        ).one()

        till = (
            await db.execute(
                select(Till)
                .where(
                    Till.device_id == device.id,
                    Till.status == TillStatusEnum.OPEN.value,
                )
                .order_by(Till.opened_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        till_user: str | None = None
        if till is not None:
            operator = await db.get(User, till.user_id)
            if operator is not None:
                till_user = operator.display_name or operator.email

        online = (
            device.last_seen_at is not None
            and (now - device.last_seen_at).total_seconds() <= OFFLINE_AFTER_SECONDS
        )

        rows.append(
            TerminalLive(
                device_id=device.id,
                name=device.name,
                reference=device.reference,
                type=device.type,
                status=device.status,
                branch_id=device.branch_id,
                branch_name=branch_names.get(device.branch_id, "Unknown"),
                last_seen_at=device.last_seen_at,
                is_online=online,
                app_version=device.app_version,
                os_version=device.os_version,
                model_identifier=device.model_identifier,
                open_till_id=till.id if till else None,
                open_till_user=till_user,
                open_till_opened_at=till.opened_at if till else None,
                active_orders=int(active[0] or 0),
                active_orders_amount=Decimal(str(active[1] or 0)),
                orders_today=int(today[0] or 0),
                sales_today=Decimal(str(today[1] or 0)),
            )
        )

    return TerminalsDashboard(
        terminals=len(rows),
        online=sum(1 for r in rows if r.is_online),
        offline=sum(1 for r in rows if not r.is_online),
        open_tills=sum(1 for r in rows if r.open_till_id is not None),
        orders_today=sum(r.orders_today for r in rows),
        sales_today=sum((r.sales_today for r in rows), start=Decimal("0")),
        devices=rows,
    )


@dashboard_router.get("/inventory")
async def inventory_dashboard(
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("dashboard.access")),
):
    """Stock health at a glance: value on hand and what needs reordering."""

    stmt = (
        select(InventoryLevel, InventoryItem)
        .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
        .where(
            InventoryItem.deleted_at.is_(None),
            InventoryItem.is_active.is_(True),
        )
    )
    if branch_id:
        stmt = stmt.join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id).where(
            Warehouse.branch_id == branch_id
        )

    total_value = Decimal("0")
    below = 0
    out_of_stock = 0
    tracked = 0
    for level, item in (await db.execute(stmt)).all():
        tracked += 1
        quantity = Decimal(str(level.quantity))
        total_value += quantity * Decimal(str(level.average_cost))
        if quantity <= 0:
            out_of_stock += 1
        elif quantity < Decimal(str(item.minimum_level)):
            below += 1

    return {
        "items_tracked": tracked,
        "stock_value": money(total_value),
        "below_minimum": below,
        "out_of_stock": out_of_stock,
    }


__all__ = [
    "TransferTemplateItemInput",
    "TransferTemplateItemResponse",
    "TransferTemplateResponse",
    "TransferTemplateUpsert",
    "dashboard_router",
    "notification_rules_router",
    "production_router",
    "transfer_orders_router",
]
