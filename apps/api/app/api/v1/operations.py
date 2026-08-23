"""
Transfer orders, production, spot checks, reservations, notification rules,
and the live branches dashboard.

These close the last verified gaps against the Foodics audit — see
docs/foodics-coverage-matrix.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.core.exceptions import BadRequestError
from app.core.money import money
from app.core.permissions import require
from app.models import (
    Branch,
    Device,
    InventoryItem,
    InventoryLevel,
    NotificationRule,
    Order,
    PosOrderStatusEnum,
    PosTable,
    Section,
    TableStatusEnum,
    Till,
    TillStatusEnum,
    TransferOrder,
    TransferOrderItem,
    Warehouse,
)
from app.models.base import utcnow
from app.models.user import User
from app.services import (
    business_day_service,
    crud_service,
    transfer_service,
)

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
    item_name: str | None = None
    item_sku: str | None = None


class TransferOrderResponse(ORMModel):
    id: uuid.UUID
    reference: str
    status: str
    branch_id: uuid.UUID
    source_branch_id: uuid.UUID
    business_date: str
    required_date: date | None
    notes: str | None
    submitted_at: datetime | None
    responded_at: datetime | None
    sent_transaction_id: uuid.UUID | None
    received_transaction_id: uuid.UUID | None
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
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require("inventory.transfers.manage")),
):
    stmt = select(TransferOrder)
    if branch_id:
        stmt = stmt.where(TransferOrder.branch_id == branch_id)
    if source_branch_id:
        stmt = stmt.where(TransferOrder.source_branch_id == source_branch_id)
    if status_filter:
        stmt = stmt.where(TransferOrder.status == status_filter)
    stmt = stmt.order_by(TransferOrder.created_at.desc()).limit(limit)
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_transfer(db, o) for o in orders]


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
    await crud_service.get_or_404(db, Branch, data.source_branch_id)

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
    received = {line.transfer_order_item_id: line.quantity for line in data.lines}
    await transfer_service.receive_transfer(
        db, order=order, user=user, received=received or None
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
    "dashboard_router",
    "notification_rules_router",
    "production_router",
    "transfer_orders_router",
]
