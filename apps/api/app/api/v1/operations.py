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
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.money import money
from app.core.permissions import require
from app.models import (
    Branch,
    Device,
    InventoryItem,
    InventoryLevel,
    InventoryTransferTemplate,
    NotificationRule,
    Order,
    PosOrderStatusEnum,
    PosTable,
    Section,
    TableStatusEnum,
    Till,
    TillStatusEnum,
    TransferKindEnum,
    TransferOrder,
    TransferOrderItem,
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
from app.services import crud_service, push_service
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


# ─── Transfer orders ──────────────────────────────────────────────────────────

transfer_orders_router = APIRouter()


class TransferLineInput(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    unit: Literal["storage", "ingredient"] = "storage"
    notes: str | None = None


class TransferOrderCreate(BaseModel):
    branch_id: uuid.UUID
    source_branch_id: uuid.UUID
    warehouse_id: uuid.UUID | None = None
    source_warehouse_id: uuid.UUID | None = None
    required_date: date | None = None
    notes: str | None = None
    items: list[TransferLineInput] = Field(min_length=1)


class QuantityDecision(BaseModel):
    transfer_order_item_id: uuid.UUID
    quantity: Decimal = Field(ge=0)


class AcceptTransfer(BaseModel):
    lines: list[QuantityDecision] = Field(default_factory=list)


class TransferOrderLineResponse(ORMModel):
    id: uuid.UUID
    item_id: uuid.UUID
    quantity: Decimal
    approved_quantity: Decimal | None
    sent_quantity: Decimal
    received_quantity: Decimal
    unit: str
    notes: str | None
    variance_reason: str | None = None
    item_name: str | None = None
    item_sku: str | None = None


class TransferOrderResponse(ORMModel):
    id: uuid.UUID
    reference: str
    status: str
    kind: str
    branch_id: uuid.UUID
    source_branch_id: uuid.UUID
    business_date: str
    required_date: date | None
    notes: str | None
    submitted_at: datetime | None
    responded_at: datetime | None
    sent_transaction_id: uuid.UUID | None
    received_transaction_id: uuid.UUID | None
    #: Provenance — the transfer template this order was raised from and its version
    #: at raise time, so the admin detail page can show "raised from template v3".
    #: Null for a return or an ad-hoc transfer raised without a template.
    template_id: uuid.UUID | None = None
    template_version: int | None = None
    created_at: datetime
    items: list[TransferOrderLineResponse] = []


async def _serialise_transfer(
    db: AsyncSession, order: TransferOrder
) -> TransferOrderResponse:
    payload = TransferOrderResponse.model_validate(order)
    ids = {line.item_id for line in order.items}
    if ids:
        rows = (
            (await db.execute(select(InventoryItem).where(InventoryItem.id.in_(ids))))
            .scalars()
            .all()
        )
        lookup = {r.id: r for r in rows}
        for line in payload.items:
            item = lookup.get(line.item_id)
            if item:
                line.item_name = item.name
                line.item_sku = item.sku
    return payload


@transfer_orders_router.get("", response_model=list[TransferOrderResponse])
async def list_transfer_orders(
    branch_id: uuid.UUID | None = None,
    source_branch_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    # The admin log fetches the branch's whole history for client-side paging, so
    # allow up to the console's max page size (2000) rather than capping at 1000.
    limit: int = Query(100, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    stmt = select(TransferOrder)
    if branch_id:
        await access_service.assert_branch_access(db, user, branch_id)
        stmt = stmt.where(TransferOrder.branch_id == branch_id)
    if source_branch_id:
        await access_service.assert_branch_access(db, user, source_branch_id)
        stmt = stmt.where(TransferOrder.source_branch_id == source_branch_id)
    if (
        not branch_id
        and not source_branch_id
        and not (user.is_admin or (user.role and user.role.is_super_admin))
    ):
        allowed = access_service.branch_ids_for(user)
        stmt = stmt.where(
            or_(
                TransferOrder.branch_id.in_(allowed),
                TransferOrder.source_branch_id.in_(allowed),
            )
        )
    if status_filter:
        stmt = stmt.where(TransferOrder.status == status_filter)
    stmt = stmt.order_by(TransferOrder.created_at.desc()).limit(limit)
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_transfer(db, o) for o in orders]


@transfer_orders_router.get("/{order_id}", response_model=TransferOrderResponse)
async def get_transfer_order(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """One transfer or return, both legs — the ledger's transfer source links here
    and the console's detail page reads it. Visible to a user with access to
    either end of the transfer."""
    order = await transfer_service.load_transfer_order(db, order_id)
    if not (user.is_admin or (user.role and user.role.is_super_admin)):
        allowed = set(access_service.branch_ids_for(user))
        if order.branch_id not in allowed and order.source_branch_id not in allowed:
            await access_service.assert_branch_access(db, user, order.branch_id)
    return await _serialise_transfer(db, order)


@transfer_orders_router.post(
    "", response_model=TransferOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_transfer_order(
    data: TransferOrderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    if data.branch_id == data.source_branch_id:
        raise BadRequestError("A branch cannot transfer to itself")

    destination = await crud_service.get_or_404(db, Branch, data.branch_id)
    await access_service.assert_branch_access(db, user, data.branch_id)
    await crud_service.get_or_404(db, Branch, data.source_branch_id)
    if data.warehouse_id:
        await inventory_service.assert_warehouse_for_branch(
            db, data.warehouse_id, data.branch_id
        )
    if data.source_warehouse_id:
        await inventory_service.assert_warehouse_for_branch(
            db, data.source_warehouse_id, data.source_branch_id
        )

    order = TransferOrder(
        reference=await transfer_service.next_transfer_reference(db),
        branch_id=data.branch_id,
        source_branch_id=data.source_branch_id,
        warehouse_id=data.warehouse_id,
        source_warehouse_id=data.source_warehouse_id,
        business_date=await business_day_service.current_business_date(db, destination),
        required_date=data.required_date,
        notes=data.notes,
        creator_id=user.id,
    )
    db.add(order)
    await db.flush()

    for line in data.items:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        db.add(
            TransferOrderItem(
                transfer_order_id=order.id,
                item_id=line.item_id,
                quantity=line.quantity,
                unit=line.unit,
                conversion_factor=(
                    Decimal("1")
                    if line.unit == "ingredient"
                    else Decimal(str(item.storage_to_ingredient_factor))
                ),
                notes=line.notes,
            )
        )
    await db.flush()
    return await _serialise_transfer(
        db, await transfer_service.load_transfer_order(db, order.id)
    )


@transfer_orders_router.post("/{order_id}/submit", response_model=TransferOrderResponse)
async def submit_transfer(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    order = await transfer_service.load_transfer_order(db, order_id)
    await access_service.assert_branch_access(db, user, order.branch_id)
    await transfer_service.submit_transfer_order(db, order=order, user=user)
    return await _serialise_transfer(db, order)


@transfer_orders_router.post("/{order_id}/accept", response_model=TransferOrderResponse)
async def accept_transfer(
    order_id: uuid.UUID,
    data: AcceptTransfer,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Accept a request, optionally granting less than was asked for."""
    order = await transfer_service.load_transfer_order(db, order_id)
    await access_service.assert_branch_access(db, user, order.source_branch_id)
    approved = {line.transfer_order_item_id: line.quantity for line in data.lines}
    await transfer_service.accept_transfer_order(
        db, order=order, user=user, approved=approved or None
    )
    return await _serialise_transfer(db, order)


@transfer_orders_router.post(
    "/{order_id}/decline", response_model=TransferOrderResponse
)
async def decline_transfer(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    order = await transfer_service.load_transfer_order(db, order_id)
    await access_service.assert_branch_access(db, user, order.source_branch_id)
    await transfer_service.decline_transfer_order(db, order=order, user=user)
    return await _serialise_transfer(db, order)


@transfer_orders_router.post("/{order_id}/send", response_model=TransferOrderResponse)
async def send_transfer(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Ship the goods — decrements the source location."""
    order = await transfer_service.load_transfer_order(db, order_id)
    await access_service.assert_branch_access(db, user, order.source_branch_id)
    await transfer_service.send_transfer(db, order=order, user=user)
    return await _serialise_transfer(db, order)


@transfer_orders_router.post(
    "/{order_id}/receive", response_model=TransferOrderResponse
)
async def receive_transfer(
    order_id: uuid.UUID,
    data: AcceptTransfer,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Book the goods in. A shortfall against what was sent stays visible."""
    order = await transfer_service.load_transfer_order(db, order_id)
    await access_service.assert_branch_access(db, user, order.branch_id)
    received = {line.transfer_order_item_id: line.quantity for line in data.lines}
    await transfer_service.receive_transfer(
        db, order=order, user=user, received=received or None
    )
    return await _serialise_transfer(db, order)


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
        for line in payload.items:
            item = lookup.get(line.item_id)
            if item:
                line.item_name = item.name
                line.item_sku = item.sku
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


class PosTransferLineInput(BaseModel):
    item_id: uuid.UUID
    quantity: Decimal = Field(gt=0)
    unit: Literal["storage", "ingredient"] = "storage"
    variance_reason: str | None = None


class PosTransferCreate(BaseModel):
    source_branch_id: uuid.UUID
    destination_branch_id: uuid.UUID
    notes: str | None = None
    #: The template this transfer was raised from, if any. When present the exact
    #: template version is snapshotted onto the order for provenance; absent for an
    #: ad-hoc transfer, and the order's template columns stay null.
    template_id: uuid.UUID | None = None
    #: A stable token so a retried create+send does not ship the box twice.
    client_request_id: str | None = Field(None, max_length=64)
    lines: list[PosTransferLineInput] = Field(min_length=1)


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


class PosTransferReceiveLine(BaseModel):
    transfer_order_item_id: uuid.UUID
    received_quantity: Decimal = Field(ge=0)
    reason: str | None = None


class PosTransferReceive(BaseModel):
    lines: list[PosTransferReceiveLine] = Field(default_factory=list)


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
        )
        .order_by(InventoryItem.name)
    )
    agg: dict[uuid.UUID, PosOnHandResponse] = {}
    for level, item in (await db.execute(stmt)).all():
        quantity = Decimal(str(level.quantity))
        existing = agg.get(item.id)
        if existing is not None:
            existing.quantity += quantity
        else:
            agg[item.id] = PosOnHandResponse(
                item_id=item.id,
                quantity=quantity,
                item_name=item.name,
                item_sku=item.sku,
                ingredient_unit=item.ingredient_unit,
            )
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


@pos_transfers_router.get(
    "/transfers/incoming", response_model=list[TransferOrderResponse]
)
async def pos_incoming_transfers(
    branch_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Transfers and returns sent to this branch and not yet booked in — the list
    the receiving tills work from."""
    await access_service.assert_branch_access(db, user, branch_id)
    stmt = (
        select(TransferOrder)
        .where(
            TransferOrder.branch_id == branch_id,
            TransferOrder.sent_transaction_id.isnot(None),
            TransferOrder.received_transaction_id.is_(None),
        )
        .order_by(TransferOrder.created_at.desc())
    )
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_transfer(db, o) for o in orders]


@pos_transfers_router.post(
    "/transfers",
    response_model=TransferOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def pos_create_transfer(
    data: PosTransferCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Create and immediately ship a transfer from the current branch. Auto-accepted
    (the sender decides what leaves) and auto-sent — the source stock leaves now."""
    await access_service.assert_branch_access(db, user, data.source_branch_id)
    source = await crud_service.get_or_404(db, Branch, data.source_branch_id)
    destination = await crud_service.get_or_404(db, Branch, data.destination_branch_id)
    order = await transfer_service.create_and_send(
        db,
        source_branch=source,
        destination_branch=destination,
        user=user,
        lines=data.lines,
        kind=TransferKindEnum.TRANSFER.value,
        notes=data.notes,
        client_request_id=data.client_request_id,
        template_id=data.template_id,
    )
    # Tell the receiving branch's tills, the same way a new website order is
    # announced — quietly, as a badge on POS actions rather than an alarm. A
    # non-POS branch has no tills and its receive was auto-completed above, so it
    # is not notified.
    if getattr(destination, "uses_pos", True):
        await push_service.notify_transfer_created(
            db,
            destination_branch_id=destination.id,
            reference=order.reference,
            item_count=len(data.lines),
            kind=TransferKindEnum.TRANSFER.value,
        )
    return await _serialise_transfer(db, order)


@pos_transfers_router.post(
    "/transfers/{order_id}/receive", response_model=TransferOrderResponse
)
async def pos_receive_transfer(
    order_id: uuid.UUID,
    data: PosTransferReceive,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Book in what arrived — short or over. The difference stays on the record with
    the receiver's reason; no separate movement is posted (the send already moved
    the stock)."""
    order = await transfer_service.load_transfer_order(db, order_id)
    await access_service.assert_branch_access(db, user, order.branch_id)
    received = {
        line.transfer_order_item_id: line.received_quantity for line in data.lines
    }
    reasons = {
        line.transfer_order_item_id: line.reason
        for line in data.lines
        if line.reason is not None
    }
    await transfer_service.receive_transfer(
        db, order=order, user=user, received=received or None, reasons=reasons or None
    )
    return await _serialise_transfer(
        db, await transfer_service.load_transfer_order(db, order_id)
    )


@pos_transfers_router.post(
    "/returns",
    response_model=TransferOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def pos_create_return(
    data: PosReturnCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require("inventory.transfers.manage")),
):
    """Send goods back to this branch's return branch (surplus, expired, damaged).

    A return is a transfer whose destination is fixed by the branch's
    ``return_branch_id`` — the till never picks it — and whose every line carries a
    reason. It runs the identical create+send+receive flow, so the return branch
    receives it exactly as it would a transfer, and it is received in that same
    "to receive" list, distinguished by its ``return`` kind.
    """
    await access_service.assert_branch_access(db, user, data.source_branch_id)
    source = await crud_service.get_or_404(db, Branch, data.source_branch_id)
    if source.return_branch_id is None:
        raise BadRequestError(
            "This branch has no return branch configured. Set one in the admin console."
        )
    destination = await crud_service.get_or_404(db, Branch, source.return_branch_id)
    order = await transfer_service.create_and_send(
        db,
        source_branch=source,
        destination_branch=destination,
        user=user,
        lines=data.lines,
        kind=TransferKindEnum.RETURN.value,
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
    return await _serialise_transfer(db, order)


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
        .where(InventoryItem.deleted_at.is_(None))
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
