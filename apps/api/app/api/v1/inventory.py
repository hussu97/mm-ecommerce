"""Inventory: items, levels, suppliers, transactions, purchase orders, recipes."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_admin_user, get_current_active_user, get_db
from app.core.exceptions import BadRequestError, ConflictError, ForbiddenError
from app.models import (
    Branch,
    InventoryCategory,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    ModifierOption,
    ModifierOptionIngredient,
    Product,
    ProductIngredient,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatusEnum,
    Supplier,
    SupplierItem,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.base import utcnow
from app.models.user import User
from app.schemas.inventory import (
    CostAdjustmentRequest,
    CostAdjustmentResponse,
    WasteRequest,
    InventoryCategoryCreate,
    InventoryCategoryResponse,
    InventoryCategoryUpdate,
    InventoryItemCreate,
    InventoryItemResponse,
    InventoryItemUpdate,
    InventoryLevelResponse,
    InventoryTransactionCreate,
    InventoryTransactionResponse,
    PurchaseOrderCreate,
    PurchaseOrderResponse,
    PurchaseOrderUpdate,
    QuantityAdjustmentRequest,
    ReceivePurchaseOrderRequest,
    RecipeLineResponse,
    RecipeResponse,
    RecipeUpsert,
    SupplierCreate,
    SupplierItemResponse,
    SupplierItemUpsert,
    SupplierResponse,
    SupplierUpdate,
    WarehouseCreate,
    WarehouseResponse,
    WarehouseUpdate,
)
from app.services import (
    audit_service,
    business_day_service,
    crud_service,
    inventory_service,
)

from .pos_config import build_crud_router

router = APIRouter()


def _require(user: User, permission: str) -> None:
    if not user.can(permission):
        raise ForbiddenError(f"You do not have permission to {permission}")


# ─── Simple CRUD ──────────────────────────────────────────────────────────────

categories_router = build_crud_router(
    model=InventoryCategory,
    create_schema=InventoryCategoryCreate,
    update_schema=InventoryCategoryUpdate,
    response_schema=InventoryCategoryResponse,
    entity_type="inventory_category",
)

suppliers_router = build_crud_router(
    model=Supplier,
    create_schema=SupplierCreate,
    update_schema=SupplierUpdate,
    response_schema=SupplierResponse,
    entity_type="supplier",
)


# ─── Warehouses ───────────────────────────────────────────────────────────────

warehouses_router = APIRouter()


@warehouses_router.get("", response_model=list[WarehouseResponse])
async def list_warehouses(
    branch_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    filters = [Warehouse.branch_id == branch_id] if branch_id else []
    return await crud_service.list_all(db, Warehouse, filters=filters)


@warehouses_router.post(
    "", response_model=WarehouseResponse, status_code=status.HTTP_201_CREATED
)
async def create_warehouse(
    data: WarehouseCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    await crud_service.get_or_404(db, Branch, data.branch_id)
    warehouse = await crud_service.create(db, Warehouse, data)
    if warehouse.is_default:
        await _clear_other_default_warehouses(db, warehouse)
    return warehouse


@warehouses_router.put("/{warehouse_id}", response_model=WarehouseResponse)
async def update_warehouse(
    warehouse_id: uuid.UUID,
    data: WarehouseUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    warehouse = await crud_service.get_or_404(db, Warehouse, warehouse_id)
    warehouse = await crud_service.update(db, warehouse, data)
    if warehouse.is_default:
        await _clear_other_default_warehouses(db, warehouse)
    return warehouse


@warehouses_router.delete("/{warehouse_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_warehouse(
    warehouse_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_admin_user),
):
    warehouse = await crud_service.get_or_404(db, Warehouse, warehouse_id)
    stock = (
        (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.warehouse_id == warehouse_id,
                    InventoryLevel.quantity != 0,
                )
            )
        )
        .scalars()
        .first()
    )
    if stock is not None:
        raise ConflictError(
            "This warehouse still holds stock — transfer it out before deleting"
        )
    await crud_service.soft_delete(db, warehouse)


async def _clear_other_default_warehouses(
    db: AsyncSession, warehouse: Warehouse
) -> None:
    stmt = select(Warehouse).where(
        Warehouse.branch_id == warehouse.branch_id,
        Warehouse.is_default.is_(True),
        Warehouse.id != warehouse.id,
    )
    for other in (await db.execute(stmt)).scalars().all():
        other.is_default = False
    await db.flush()


# ─── Items and levels ─────────────────────────────────────────────────────────

items_router = APIRouter()


@items_router.get("", response_model=list[InventoryItemResponse])
async def list_items(
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    include_inactive: bool = True,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.items.read")
    filters = []
    if category_id:
        filters.append(InventoryItem.category_id == category_id)
    if search:
        pattern = f"%{search.lower()}%"
        filters.append(
            InventoryItem.name.ilike(pattern) | InventoryItem.sku.ilike(pattern)
        )
    return await crud_service.list_all(
        db, InventoryItem, include_inactive=include_inactive, filters=filters
    )


@items_router.post(
    "", response_model=InventoryItemResponse, status_code=status.HTTP_201_CREATED
)
async def create_item(
    request: Request,
    data: InventoryItemCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.items.manage")
    if await crud_service.reference_taken(db, InventoryItem, "sku", data.sku):
        raise ConflictError(f"SKU '{data.sku}' is already in use")
    item = await crud_service.create(db, InventoryItem, data)
    await audit_service.log_action(
        db,
        action="CREATE",
        entity_type="inventory_item",
        entity_id=str(item.id),
        entity_label=item.name,
        admin=user,
        changes={"created": data.model_dump(mode="json")},
        request=request,
    )
    return item


@items_router.get("/{item_id}", response_model=InventoryItemResponse)
async def get_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.items.read")
    return await crud_service.get_or_404(
        db, InventoryItem, item_id, include_deleted=True
    )


@items_router.put("/{item_id}", response_model=InventoryItemResponse)
async def update_item(
    request: Request,
    item_id: uuid.UUID,
    data: InventoryItemUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.items.manage")
    item = await crud_service.get_or_404(db, InventoryItem, item_id)
    if data.sku and await crud_service.reference_taken(
        db, InventoryItem, "sku", data.sku, exclude_id=item_id
    ):
        raise ConflictError(f"SKU '{data.sku}' is already in use")
    item = await crud_service.update(db, item, data)
    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="inventory_item",
        entity_id=str(item_id),
        entity_label=item.name,
        admin=user,
        changes={"data": data.model_dump(mode="json", exclude_unset=True)},
        request=request,
    )
    return item


@items_router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(
    item_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.items.manage")
    item = await crud_service.get_or_404(db, InventoryItem, item_id)
    await crud_service.soft_delete(db, item)


# ─── Levels ───────────────────────────────────────────────────────────────────

levels_router = APIRouter()


@levels_router.get("", response_model=list[InventoryLevelResponse])
async def list_levels(
    branch_id: uuid.UUID | None = None,
    warehouse_id: uuid.UUID | None = None,
    below_minimum_only: bool = False,
    limit: int = Query(500, ge=1, le=5000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """The inventory levels report: what is on hand and what needs reordering."""
    _require(user, "reports.inventory_levels")

    stmt = (
        select(InventoryLevel, InventoryItem)
        .join(InventoryItem, InventoryItem.id == InventoryLevel.item_id)
        .where(InventoryItem.deleted_at.is_(None))
    )
    if warehouse_id:
        stmt = stmt.where(InventoryLevel.warehouse_id == warehouse_id)
    elif branch_id:
        stmt = stmt.join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id).where(
            Warehouse.branch_id == branch_id
        )
    stmt = stmt.order_by(InventoryItem.name).limit(limit)

    payload: list[InventoryLevelResponse] = []
    for level, item in (await db.execute(stmt)).all():
        below = Decimal(str(level.quantity)) < Decimal(str(item.minimum_level))
        if below_minimum_only and not below:
            continue
        row = InventoryLevelResponse.model_validate(level)
        row.item_name = item.name
        row.item_sku = item.sku
        row.ingredient_unit = item.ingredient_unit
        row.minimum_level = item.minimum_level
        row.par_level = item.par_level
        row.total_value = level.total_value
        row.is_below_minimum = below
        payload.append(row)
    return payload


# ─── Transactions ─────────────────────────────────────────────────────────────

transactions_router = APIRouter()


def _serialise_transaction(
    transaction: InventoryTransaction, names: dict[uuid.UUID, InventoryItem]
) -> InventoryTransactionResponse:
    payload = InventoryTransactionResponse.model_validate(transaction)
    for line in payload.items:
        item = names.get(line.item_id)
        if item is not None:
            line.item_name = item.name
            line.item_sku = item.sku
    return payload


async def _item_lookup(
    db: AsyncSession, transaction: InventoryTransaction
) -> dict[uuid.UUID, InventoryItem]:
    ids = {line.item_id for line in transaction.items}
    if not ids:
        return {}
    rows = (
        (await db.execute(select(InventoryItem).where(InventoryItem.id.in_(ids))))
        .scalars()
        .all()
    )
    return {row.id: row for row in rows}


@transactions_router.get("", response_model=list[InventoryTransactionResponse])
async def list_transactions(
    branch_id: uuid.UUID | None = None,
    type: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    business_date: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.inventory_transactions")
    stmt = select(InventoryTransaction).options(
        selectinload(InventoryTransaction.items)
    )
    if branch_id:
        stmt = stmt.where(InventoryTransaction.branch_id == branch_id)
    if type:
        stmt = stmt.where(InventoryTransaction.type == type)
    if status_filter:
        stmt = stmt.where(InventoryTransaction.status == status_filter)
    if business_date:
        stmt = stmt.where(InventoryTransaction.business_date == business_date)
    stmt = stmt.order_by(InventoryTransaction.created_at.desc()).limit(limit)

    transactions = list((await db.execute(stmt)).scalars().unique().all())
    payload = []
    for transaction in transactions:
        payload.append(
            _serialise_transaction(transaction, await _item_lookup(db, transaction))
        )
    return payload


@transactions_router.post(
    "",
    response_model=InventoryTransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_transaction(
    data: InventoryTransactionCreate,
    post: bool = Query(True, description="Post immediately, moving stock"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.items.manage")
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    business_date = await business_day_service.current_business_date(db, branch)

    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(db, data.type),
        type=data.type,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=data.branch_id,
        warehouse_id=data.warehouse_id,
        other_branch_id=data.other_branch_id,
        other_warehouse_id=data.other_warehouse_id,
        supplier_id=data.supplier_id,
        reason_id=data.reason_id,
        business_date=business_date,
        invoice_number=data.invoice_number,
        invoice_date=data.invoice_date,
        additional_cost=data.additional_cost,
        paid_tax=data.paid_tax,
        notes=data.notes,
        creator_id=user.id,
    )
    db.add(transaction)
    await db.flush()

    for line in data.items:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=line.item_id,
                quantity=line.quantity,
                unit=line.unit,
                conversion_factor=(
                    Decimal("1")
                    if line.unit == "ingredient"
                    else Decimal(str(item.storage_to_ingredient_factor))
                ),
                unit_cost=line.unit_cost,
                notes=line.notes,
            )
        )
    await db.flush()
    transaction = await inventory_service.load_transaction(db, transaction.id)

    if post:
        transaction = await inventory_service.post_transaction(
            db, transaction=transaction, user=user
        )
        transaction = await inventory_service.load_transaction(db, transaction.id)

    return _serialise_transaction(transaction, await _item_lookup(db, transaction))


@transactions_router.get(
    "/{transaction_id}", response_model=InventoryTransactionResponse
)
async def get_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "reports.inventory_transactions")
    transaction = await inventory_service.load_transaction(db, transaction_id)
    return _serialise_transaction(transaction, await _item_lookup(db, transaction))


@transactions_router.post(
    "/{transaction_id}/post", response_model=InventoryTransactionResponse
)
async def post_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Move the stock for a draft transaction."""
    _require(user, "inventory.items.manage")
    transaction = await inventory_service.load_transaction(db, transaction_id)
    transaction = await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )
    transaction = await inventory_service.load_transaction(db, transaction.id)
    return _serialise_transaction(transaction, await _item_lookup(db, transaction))


@transactions_router.post("/adjust", response_model=InventoryTransactionResponse)
async def adjust_quantity(
    data: QuantityAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Single-line signed adjustment — waste, breakage, or a correction."""
    _require(user, "inventory.quantity_adjustment.create")
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    transaction = await inventory_service.adjust_level(
        db,
        branch=branch,
        user=user,
        item_id=data.item_id,
        quantity_delta=data.quantity_delta,
        reason_id=data.reason_id,
        notes=data.notes,
    )
    transaction = await inventory_service.load_transaction(db, transaction.id)
    return _serialise_transaction(transaction, await _item_lookup(db, transaction))


# ─── Purchase orders ──────────────────────────────────────────────────────────

purchase_orders_router = APIRouter()


async def _serialise_po(
    db: AsyncSession, purchase_order: PurchaseOrder
) -> PurchaseOrderResponse:
    payload = PurchaseOrderResponse.model_validate(purchase_order)
    ids = {line.item_id for line in purchase_order.items}
    items = (
        (await db.execute(select(InventoryItem).where(InventoryItem.id.in_(ids))))
        .scalars()
        .all()
        if ids
        else []
    )
    lookup = {i.id: i for i in items}
    for line, source in zip(payload.items, purchase_order.items):
        line.outstanding_quantity = source.outstanding_quantity
        item = lookup.get(line.item_id)
        if item is not None:
            line.item_name = item.name
            line.item_sku = item.sku
    supplier = await db.get(Supplier, purchase_order.supplier_id)
    payload.supplier_name = supplier.name if supplier else None
    return payload


async def _load_po(db: AsyncSession, po_id: uuid.UUID) -> PurchaseOrder:
    return await crud_service.get_or_404(
        db, PurchaseOrder, po_id, options=[selectinload(PurchaseOrder.items)]
    )


@purchase_orders_router.get("", response_model=list[PurchaseOrderResponse])
async def list_purchase_orders(
    branch_id: uuid.UUID | None = None,
    supplier_id: uuid.UUID | None = None,
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.purchase_orders.view_approved")
    stmt = select(PurchaseOrder).options(selectinload(PurchaseOrder.items))
    if branch_id:
        stmt = stmt.where(PurchaseOrder.branch_id == branch_id)
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if status_filter:
        stmt = stmt.where(PurchaseOrder.status == status_filter)
    stmt = stmt.order_by(PurchaseOrder.created_at.desc()).limit(limit)
    orders = list((await db.execute(stmt)).scalars().unique().all())
    return [await _serialise_po(db, o) for o in orders]


@purchase_orders_router.post(
    "", response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_purchase_order(
    data: PurchaseOrderCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.purchase_orders.create")
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    await crud_service.get_or_404(db, Supplier, data.supplier_id)
    business_date = await business_day_service.current_business_date(db, branch)

    count = len((await db.execute(select(PurchaseOrder.id))).scalars().all())
    purchase_order = PurchaseOrder(
        reference=f"PO-{count + 1:06d}",
        status=PurchaseOrderStatusEnum.DRAFT.value,
        supplier_id=data.supplier_id,
        branch_id=data.branch_id,
        warehouse_id=data.warehouse_id,
        business_date=business_date,
        delivery_date=data.delivery_date,
        additional_cost=data.additional_cost,
        notes=data.notes,
        creator_id=user.id,
    )
    db.add(purchase_order)
    await db.flush()
    await _replace_po_lines(db, purchase_order, data.items)
    return await _serialise_po(db, await _load_po(db, purchase_order.id))


async def _replace_po_lines(
    db: AsyncSession, purchase_order: PurchaseOrder, lines
) -> None:
    await db.execute(
        delete(PurchaseOrderItem).where(
            PurchaseOrderItem.purchase_order_id == purchase_order.id
        )
    )
    total = Decimal("0")
    for line in lines:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        line_total = Decimal(str(line.quantity)) * Decimal(str(line.unit_cost))
        total += line_total
        db.add(
            PurchaseOrderItem(
                purchase_order_id=purchase_order.id,
                item_id=line.item_id,
                quantity=line.quantity,
                unit=line.unit,
                conversion_factor=(
                    Decimal("1")
                    if line.unit == "ingredient"
                    else Decimal(str(item.storage_to_ingredient_factor))
                ),
                unit_cost=line.unit_cost,
                total_cost=line_total,
            )
        )
    purchase_order.total_cost = total + Decimal(
        str(purchase_order.additional_cost or 0)
    )
    await db.flush()


@purchase_orders_router.get("/{po_id}", response_model=PurchaseOrderResponse)
async def get_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.purchase_orders.view_approved")
    return await _serialise_po(db, await _load_po(db, po_id))


@purchase_orders_router.put("/{po_id}", response_model=PurchaseOrderResponse)
async def update_purchase_order(
    po_id: uuid.UUID,
    data: PurchaseOrderUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.purchase_orders.create")
    purchase_order = await _load_po(db, po_id)
    if purchase_order.status in (
        PurchaseOrderStatusEnum.CLOSED.value,
        PurchaseOrderStatusEnum.PARTIALLY_RECEIVED.value,
    ):
        raise ConflictError("A received purchase order can no longer be edited")

    await crud_service.update(
        db, purchase_order, data.model_dump(exclude={"items"}, exclude_unset=True)
    )
    if data.items is not None:
        await _replace_po_lines(db, purchase_order, data.items)
    return await _serialise_po(db, await _load_po(db, po_id))


@purchase_orders_router.post("/{po_id}/submit", response_model=PurchaseOrderResponse)
async def submit_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.purchase_orders.submit")
    purchase_order = await _load_po(db, po_id)
    if purchase_order.status != PurchaseOrderStatusEnum.DRAFT.value:
        raise ConflictError(
            f"Only draft orders can be submitted (this is {purchase_order.status})"
        )
    purchase_order.status = PurchaseOrderStatusEnum.PENDING.value
    purchase_order.submitter_id = user.id
    purchase_order.submitted_at = utcnow()
    await db.flush()
    return await _serialise_po(db, await _load_po(db, po_id))


@purchase_orders_router.post("/{po_id}/approve", response_model=PurchaseOrderResponse)
async def approve_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.purchase_orders.approve")
    purchase_order = await _load_po(db, po_id)
    if purchase_order.status != PurchaseOrderStatusEnum.PENDING.value:
        raise ConflictError("Only submitted orders can be approved")
    # Separation of duties: whoever raised the order cannot approve their own.
    if purchase_order.submitter_id == user.id and not user.is_admin:
        raise ForbiddenError("A purchase order must be approved by someone else")
    purchase_order.status = PurchaseOrderStatusEnum.APPROVED.value
    purchase_order.approver_id = user.id
    purchase_order.approved_at = utcnow()
    await db.flush()
    return await _serialise_po(db, await _load_po(db, po_id))


@purchase_orders_router.post("/{po_id}/decline", response_model=PurchaseOrderResponse)
async def decline_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.purchase_orders.approve")
    purchase_order = await _load_po(db, po_id)
    if purchase_order.status != PurchaseOrderStatusEnum.PENDING.value:
        raise ConflictError("Only submitted orders can be declined")
    purchase_order.status = PurchaseOrderStatusEnum.DECLINED.value
    purchase_order.approver_id = user.id
    await db.flush()
    return await _serialise_po(db, await _load_po(db, po_id))


@purchase_orders_router.post(
    "/{po_id}/receive", response_model=InventoryTransactionResponse
)
async def receive_purchase_order(
    po_id: uuid.UUID,
    data: ReceivePurchaseOrderRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Receive a delivery, in full or in part, moving stock in."""
    _require(user, "inventory.purchasing.from_po")
    purchase_order = await _load_po(db, po_id)
    received = {line.purchase_order_item_id: line.quantity for line in data.lines}
    transaction = await inventory_service.receive_purchase_order(
        db, purchase_order=purchase_order, user=user, received=received
    )
    transaction = await inventory_service.load_transaction(db, transaction.id)
    return _serialise_transaction(transaction, await _item_lookup(db, transaction))


# ─── Supplier catalogue ───────────────────────────────────────────────────────


@suppliers_router.get("/{supplier_id}/items", response_model=list[SupplierItemResponse])
async def list_supplier_items(
    supplier_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.suppliers.read")
    stmt = select(SupplierItem).where(SupplierItem.supplier_id == supplier_id)
    return list((await db.execute(stmt)).scalars().all())


@suppliers_router.put("/{supplier_id}/items", response_model=list[SupplierItemResponse])
async def set_supplier_items(
    supplier_id: uuid.UUID,
    data: list[SupplierItemUpsert],
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "inventory.suppliers.manage")
    await crud_service.get_or_404(db, Supplier, supplier_id)
    await db.execute(
        delete(SupplierItem).where(SupplierItem.supplier_id == supplier_id)
    )
    for entry in data:
        db.add(SupplierItem(supplier_id=supplier_id, **entry.model_dump()))
    await db.flush()
    return await list_supplier_items(supplier_id, db, user)


# ─── Recipes ──────────────────────────────────────────────────────────────────

recipes_router = APIRouter()


async def _recipe_response(db: AsyncSession, product_id: uuid.UUID) -> RecipeResponse:
    stmt = select(ProductIngredient).where(ProductIngredient.product_id == product_id)
    rows = list((await db.execute(stmt)).scalars().all())

    payload = RecipeResponse(product_id=product_id)
    total = Decimal("0")
    for row in rows:
        item = await db.get(InventoryItem, row.item_id)
        line_cost = Decimal(str(row.quantity)) * Decimal(str(item.cost if item else 0))
        total += line_cost
        payload.ingredients.append(
            RecipeLineResponse(
                id=row.id,
                item_id=row.item_id,
                quantity=row.quantity,
                inactive_in_order_types=row.inactive_in_order_types or [],
                item_name=item.name if item else None,
                item_sku=item.sku if item else None,
                ingredient_unit=item.ingredient_unit if item else None,
                unit_cost=item.cost if item else None,
                line_cost=line_cost,
            )
        )
    payload.total_cost = total
    return payload


@recipes_router.get("/products/{product_id}", response_model=RecipeResponse)
async def get_product_recipe(
    product_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "menu.ingredients.read")
    await crud_service.get_or_404(db, Product, product_id)
    return await _recipe_response(db, product_id)


@recipes_router.put("/products/{product_id}", response_model=RecipeResponse)
async def set_product_recipe(
    product_id: uuid.UUID,
    data: RecipeUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Replace a product's recipe. Selling the product depletes these."""
    _require(user, "menu.ingredients.manage")
    await crud_service.get_or_404(db, Product, product_id)
    await db.execute(
        delete(ProductIngredient).where(ProductIngredient.product_id == product_id)
    )
    for line in data.ingredients:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        db.add(
            ProductIngredient(
                product_id=product_id,
                item_id=line.item_id,
                quantity=line.quantity,
                inactive_in_order_types=line.inactive_in_order_types,
            )
        )
    await db.flush()
    return await _recipe_response(db, product_id)


@recipes_router.put("/modifier-options/{option_id}")
async def set_modifier_option_recipe(
    option_id: uuid.UUID,
    data: RecipeUpsert,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require(user, "menu.ingredients.manage")
    await crud_service.get_or_404(db, ModifierOption, option_id)
    await db.execute(
        delete(ModifierOptionIngredient).where(
            ModifierOptionIngredient.modifier_option_id == option_id
        )
    )
    for line in data.ingredients:
        db.add(
            ModifierOptionIngredient(
                modifier_option_id=option_id,
                item_id=line.item_id,
                quantity=line.quantity,
            )
        )
    await db.flush()
    return {"modifier_option_id": str(option_id), "ingredients": len(data.ingredients)}


__all__ = [
    "categories_router",
    "items_router",
    "levels_router",
    "purchase_orders_router",
    "recipes_router",
    "router",
    "suppliers_router",
    "transactions_router",
    "warehouses_router",
]


@transactions_router.post("/waste", response_model=InventoryTransactionResponse)
async def record_waste(
    data: WasteRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """
    Write off stock that went in the bin.

    Separate from a quantity adjustment on purpose: a correction means the
    count was wrong, waste means the food was thrown away, and only the second
    belongs in the number a kitchen is managed on.
    """
    _require(user, "inventory.quantity_adjustment.create")
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    transaction = await inventory_service.record_waste(
        db,
        branch=branch,
        user=user,
        item_id=data.item_id,
        quantity=data.quantity,
        from_production=data.from_production,
        reason_id=data.reason_id,
        notes=data.notes,
    )
    transaction = await inventory_service.load_transaction(db, transaction.id)
    return _serialise_transaction(transaction, await _item_lookup(db, transaction))


@items_router.post("/cost-adjustment", response_model=CostAdjustmentResponse)
async def adjust_cost(
    data: CostAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """Restate what stock on hand is worth, without moving any of it."""
    _require(user, "inventory.cost_adjustment.create")
    branch = await crud_service.get_or_404(db, Branch, data.branch_id)
    return await inventory_service.adjust_cost(
        db,
        branch=branch,
        item_id=data.item_id,
        warehouse_id=data.warehouse_id,
        new_average_cost=data.new_average_cost,
        user=user,
        notes=data.notes,
    )
