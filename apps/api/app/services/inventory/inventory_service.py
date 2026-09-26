"""
Inventory posting and costing.

Stock only ever moves by posting an `InventoryTransaction`. Posting is the one
place that touches `InventoryLevel`, which keeps the ledger authoritative and
makes levels rebuildable.

Costing is **FIFO**. Each receipt lays down an `InventoryCostLayer`; each issue
consumes the oldest layers first (see `cost_layer_service`), recording the true
cost of the specific receipts it drew from. `InventoryLevel.average_cost` is then
*derived* — the weighted average of what still remains — so every reader keeps
working while cost of goods is first-in, first-out rather than a blend.
`apply_movement` below still maintains the level *quantity*; the valuation half
lives in the layers.

This module also owns the **purchase-order state machine** (below). It used to
live inline in `api/v1/inventory.py`, four endpoints each opening with its own
`if status != …` and then assigning the column by hand — while the order state
machine was a service (`order_lifecycle`) and the transfer state machine was
another (`transfer_service`). Three homes for one pattern is how they drift, and
`order_lifecycle` exists precisely because the order one already had: five sets
of rules, one of which quietly skipped the refund.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, event, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)

# Aliased to the existing private names: the implementation is shared,
# the call sites stay put, and `quantity` is already a local variable in
# both of these files.
from app.core.money import money as _money
from app.core.money import quantity as _q
from app.core.money import unit_cost as _c
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.business_settings import BusinessSettings
from app.models.inventory import (
    TRANSACTION_SIGN,
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderMiscItem,
    PurchaseOrderStatusEnum,
    Supplier,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventoryItemKindEnum,
    InventoryTrackingModeEnum,
)
from app.models.order import Order
from app.models.user import User
from app.services.inventory import costing_service, po_misc_service, supplier_service
from app.services.pos import business_day_service

__all__ = [
    "PURCHASE_ORDER_MOVES",
    "adjust_level",
    "allowed_purchase_order_transitions",
    "assert_can_transition_purchase_order",
    "can_transition_purchase_order",
    "branch_warehouse_ids",
    "default_warehouse",
    "deplete_for_order",
    "level_for",
    "inventory_item_cost_for_unit",
    "assert_warehouse_for_branch",
    "line_cost_in_storage_unit",
    "next_reference",
    "next_inventory_reference",
    "post_transaction",
    "build_po_lines",
    "create_pos_purchase_order",
    "receive_purchase_order",
    "transition_purchase_order",
]

logger = logging.getLogger(__name__)


REFERENCE_PREFIX = {
    InventoryTransactionTypeEnum.PURCHASING.value: "PUR",
    InventoryTransactionTypeEnum.TRANSFER_SEND.value: "TRS",
    InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value: "TRR",
    InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value: "ADJ",
    InventoryTransactionTypeEnum.RETURN_TO_SUPPLIER.value: "RTS",
    InventoryTransactionTypeEnum.PRODUCTION.value: "PRD",
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value: "CFP",
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value: "CFO",
    InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value: "RFO",
    InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value: "WFO",
    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value: "WFP",
    InventoryTransactionTypeEnum.COST_ADJUSTMENT.value: "CAD",
    InventoryTransactionTypeEnum.INVENTORY_COUNT.value: "CNT",
    InventoryTransactionTypeEnum.OPENING_BALANCE.value: "OPN",
    InventoryTransactionTypeEnum.INTERNAL_USE.value: "INT",
    InventoryTransactionTypeEnum.EXTRA_PRODUCTION_USE.value: "EPU",
    InventoryTransactionTypeEnum.PRODUCTION_RESTATEMENT.value: "PRS",
}


async def assert_warehouse_for_branch(
    db: AsyncSession, warehouse_id: uuid.UUID, branch_id: uuid.UUID
) -> Warehouse:
    warehouse = await db.get(Warehouse, warehouse_id)
    if warehouse is None or warehouse.deleted_at is not None or not warehouse.is_active:
        raise NotFoundError("Stock container not found")
    if warehouse.branch_id != branch_id:
        raise ConflictError("Stock container does not belong to this branch")
    return warehouse


def inventory_item_cost_for_unit(item: InventoryItem, unit: str) -> Decimal:
    """The zero pre-cost fallback, in the requested unit.

    The item no longer carries a catalogue cost of its own — cost is FIFO, held in
    the item's cost layers and summarised on ``InventoryLevel.average_cost``. So an
    item with no costed stock yet is valued at 0 until its first receipt or
    production posts a real cost. Every caller here reaches this only *after*
    consulting the FIFO layer / level average for the warehouse in hand, so this is
    strictly the "no cost known" case. Kept as a function (rather than inlining a
    zero) so the ``storage``/``ingredient`` unit contract stays enforced.
    """
    if unit not in {"storage", "ingredient"}:
        raise BadRequestError(f"Unknown inventory entry unit '{unit}'")
    return _c(0)


def canonical_cost_for_unit(
    item: InventoryItem, storage_cost: Decimal, unit: str
) -> Decimal:
    """Convert a per-storage-unit moving average to an entered-unit cost.

    The ledger's canonical cost is per storage unit; a line entered in storage
    units carries it unchanged, one entered in ingredient units carries it
    divided by the factor (ingredient = storage ÷ factor for cost too).
    """
    if unit not in {"storage", "ingredient"}:
        raise BadRequestError(f"Unknown inventory entry unit '{unit}'")
    cost = Decimal(str(storage_cost))
    if unit == "storage":
        return _c(cost)
    factor = Decimal(str(item.storage_to_ingredient_factor or 1))
    if factor <= 0:
        raise BadRequestError(f"{item.name} has an invalid unit conversion factor")
    return _c(cost / factor)


def line_cost_in_storage_unit(line: InventoryTransactionItem) -> Decimal:
    """Read an immutable ledger line's cost as cost per storage unit.

    Storage is the canonical valuation unit; a storage-entered line's cost is
    already per storage, an ingredient-entered line's is per ingredient and
    multiplies by the snapshot factor to reach per storage.
    """
    if line.unit not in {"storage", "ingredient"}:
        raise BadRequestError(f"Unknown inventory entry unit '{line.unit}'")
    entered_cost = Decimal(str(line.unit_cost or 0))
    if line.unit == "storage":
        return _c(entered_cost)
    factor = Decimal(str(line.conversion_factor or 1))
    if factor <= 0:
        raise BadRequestError("A ledger line has an invalid conversion factor")
    return _c(entered_cost * factor)


async def next_reference(db: AsyncSession, transaction_type: str) -> str:
    """Human-readable sequential reference, e.g. PUR-000123."""
    prefix = REFERENCE_PREFIX.get(transaction_type, "INV")
    return await next_inventory_reference(db, prefix)


async def next_inventory_reference(db: AsyncSession, prefix: str) -> str:
    """Allocate a collision-free reference across concurrent branches."""
    value = int(
        (
            await db.execute(text("SELECT nextval('inventory_reference_sequence')"))
        ).scalar_one()
    )
    return f"{prefix}-{value:06d}"


async def default_warehouse(db: AsyncSession, branch_id: uuid.UUID) -> Warehouse:
    """
    The warehouse stock lands in for a branch, creating one on first use so a
    new branch never blocks a delivery.
    """
    branch = (
        await db.execute(select(Branch).where(Branch.id == branch_id).with_for_update())
    ).scalar_one_or_none()
    if branch is None:
        raise NotFoundError("Branch not found")

    stmt = (
        select(Warehouse)
        .where(
            Warehouse.branch_id == branch_id,
            Warehouse.deleted_at.is_(None),
            Warehouse.is_active.is_(True),
        )
        .order_by(Warehouse.is_default.desc(), Warehouse.created_at, Warehouse.id)
    )
    existing = (await db.execute(stmt)).scalars().first()
    if existing is not None:
        if not existing.is_default:
            existing.is_default = True
            await db.flush()
        return existing

    warehouse = Warehouse(
        branch_id=branch_id, name=f"{branch.name} Store", is_default=True
    )
    db.add(warehouse)
    await db.flush()
    await db.refresh(warehouse)
    return warehouse


async def branch_warehouse_ids(
    db: AsyncSession, branch_id: uuid.UUID
) -> list[uuid.UUID]:
    """The live stock containers of one branch — the scope of "this branch's cost"."""
    return list(
        (
            await db.execute(
                select(Warehouse.id).where(
                    Warehouse.branch_id == branch_id,
                    Warehouse.deleted_at.is_(None),
                    Warehouse.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )


async def level_for(
    db: AsyncSession,
    item_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> InventoryLevel:
    stmt = select(InventoryLevel).where(
        InventoryLevel.item_id == item_id,
        InventoryLevel.warehouse_id == warehouse_id,
    )
    if for_update:
        # Mutation path: hold the row so two concurrent postings cannot both
        # read the same balance and lose one movement.
        stmt = stmt.with_for_update()
    level = (await db.execute(stmt)).scalar_one_or_none()
    if level is None:
        level = InventoryLevel(
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity=Decimal("0"),
            average_cost=Decimal("0"),
        )
        db.add(level)
        await db.flush()
    return level


def apply_movement(
    level: InventoryLevel, quantity_delta: Decimal, unit_cost: Decimal | None
) -> None:
    """
    Move stock and maintain the weighted-average cost.

    Receipts blend the incoming cost into the average; issues leave it alone.
    A receipt onto a negative balance resets the average to the incoming cost
    rather than producing a nonsensical blend.
    """
    on_hand = _q(level.quantity)
    average = _c(level.average_cost)
    delta = _q(quantity_delta)

    if delta > 0 and unit_cost is not None:
        incoming_cost = _c(unit_cost)
        if on_hand <= 0:
            average = incoming_cost
        else:
            total_value = on_hand * average + delta * incoming_cost
            average = _c(total_value / (on_hand + delta))

    level.quantity = _q(on_hand + delta)
    level.average_cost = average


async def post_transaction(
    db: AsyncSession, *, transaction: InventoryTransaction, user: User | None
) -> InventoryTransaction:
    """
    Apply a draft/pending transaction to stock. Idempotent by refusal: an
    already-closed transaction cannot be posted twice.
    """
    if transaction.is_posted:
        raise ConflictError(f"{transaction.reference} has already been posted")
    if not transaction.items:
        raise BadRequestError("A transaction must have at least one line")

    # Every writer—not only order consumption—serializes on the branch before
    # it receives a posting sequence or locks level rows. PostgreSQL advisory
    # transaction locks are re-entrant, so callers that already hold this lock
    # (the source-event worker and count approval) safely pass through.
    from app.services.inventory import source_event_service

    await source_event_service.lock_branch_inventory(db, transaction.branch_id)

    sign = TRANSACTION_SIGN.get(transaction.type)
    if sign is None:
        raise BadRequestError(f"Unknown transaction type '{transaction.type}'")

    settings = (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
    branch_settings = (
        (
            await db.execute(
                select(BranchInventorySettings).where(
                    BranchInventorySettings.branch_id == transaction.branch_id
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    bootstrap_types = {
        InventoryTransactionTypeEnum.OPENING_BALANCE.value,
        InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
    }
    if (
        branch_settings is None or not branch_settings.inventory_enabled
    ) and transaction.type not in bootstrap_types:
        raise ConflictError("Inventory movements are not enabled for this branch")
    prevent_negative = bool(settings and settings.prevent_negative_stock)
    if branch_settings and (
        branch_settings.validation_mode or branch_settings.allow_negative_stock
    ):
        prevent_negative = False

    if transaction.posting_sequence is None:
        transaction.posting_sequence = int(
            (
                await db.execute(text("SELECT nextval('inventory_posting_sequence')"))
            ).scalar_one()
        )
    posting_sequence = transaction.posting_sequence

    if transaction.warehouse_id is not None:
        warehouse_id = (
            await assert_warehouse_for_branch(
                db, transaction.warehouse_id, transaction.branch_id
            )
        ).id
    else:
        warehouse_id = (await default_warehouse(db, transaction.branch_id)).id
    if transaction.other_warehouse_id is not None:
        if transaction.other_branch_id is None:
            raise BadRequestError(
                "A destination branch is required for its stock container"
            )
        await assert_warehouse_for_branch(
            db, transaction.other_warehouse_id, transaction.other_branch_id
        )

    # Lines need their ids before the costing engine can key layers on them.
    await db.flush()
    posted_at = utcnow()
    total = Decimal("0")
    costed: list[tuple[InventoryTransactionItem, InventoryLevel]] = []
    engine_lines: list = []
    produced_good_ids: set[uuid.UUID] = set()
    for line in transaction.items:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        if item.kind == InventoryItemKindEnum.PRODUCED_GOOD.value:
            produced_good_ids.add(item.id)
        if item.tracking_mode == InventoryTrackingModeEnum.PHANTOM.value:
            raise BadRequestError(
                f"{item.name} is a phantom recipe item and cannot hold stock"
            )
        if line.unit not in {"storage", "ingredient"}:
            raise BadRequestError(f"Unknown inventory entry unit '{line.unit}'")

        # Normalise into the canonical STORAGE unit using the factor snapshotted
        # on the line, falling back to the item's current factor for new lines.
        # factor = ingredient units per one storage unit (ingredient = storage ×
        # factor), so a storage-entered line passes straight through and an
        # ingredient-entered line (a recipe consumption) divides by the factor to
        # reach the grams actually taken off the shelf. Both figures are recorded
        # so a movement shows the amount in each unit.
        factor = _c(line.conversion_factor or item.storage_to_ingredient_factor or 1)
        if factor <= 0:
            raise BadRequestError(f"{item.name} has an invalid unit conversion factor")
        line.conversion_factor = factor
        entered = Decimal(str(line.quantity))
        if line.unit == "ingredient":
            storage_quantity = _q(entered / factor)
            ingredient_quantity = _q(entered)
        else:
            storage_quantity = _q(entered)
            ingredient_quantity = _q(entered * factor)
        line.quantity_in_storage_unit = storage_quantity
        line.quantity_in_ingredient_unit = ingredient_quantity
        normalised = storage_quantity

        # Adjustments and counts carry their own sign in the quantity.
        delta = normalised if sign >= 0 else -normalised
        if transaction.type in (
            InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
            InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
            InventoryTransactionTypeEnum.OPENING_BALANCE.value,
        ):
            delta = normalised

        level = await level_for(db, line.item_id, warehouse_id, for_update=True)

        if transaction.type in (
            InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
            InventoryTransactionTypeEnum.OPENING_BALANCE.value,
        ):
            # A count and an opening balance SET the balance to the counted figure
            # rather than adding it. Posting an opening balance as an addition let a
            # second opening count (or a re-run) double the stock; setting it means
            # the balance lands on the count whatever was there before. The variance
            # is what the report cares about.
            line.expected_quantity = _q(level.quantity)
            delta = _q(normalised - _q(level.quantity))

        unit_cost_canonical = line_cost_in_storage_unit(line)
        line.previous_unit_cost = _c(level.average_cost)
        if transaction.type == InventoryTransactionTypeEnum.COST_ADJUSTMENT.value:
            # Restate what stock is worth without moving any of it: the engine
            # rescales the surviving FIFO layers to the entered average.
            line.quantity = _q(level.quantity)
            line.quantity_in_storage_unit = _q(level.quantity)
            line.quantity_in_ingredient_unit = _q(Decimal(str(level.quantity)) * factor)
            delta = Decimal("0")
        elif (
            transaction.type
            == InventoryTransactionTypeEnum.PRODUCTION_RESTATEMENT.value
        ):
            # Re-costs a batch; its quantity is the batch's, for the record only.
            delta = Decimal("0")
        elif prevent_negative and delta < 0 and _q(level.quantity) + delta < 0:
            raise ConflictError(
                f"{item.name}: only {_q(level.quantity)} {item.storage_unit} "
                f"available, cannot issue {abs(delta)}"
            )

        # Quantity moves here; what it is worth is the costing engine's job,
        # run once for the whole posting below.
        apply_movement(level, delta, None)
        line.signed_quantity = _q(delta)
        costed.append((line, level))
        engine_lines.append(
            costing_service.ledger_line_for(
                transaction,
                line,
                warehouse_id=warehouse_id,
                storage_cost=unit_cost_canonical,
                posted_at=posted_at,
            )
        )
        if transaction.type == InventoryTransactionTypeEnum.INVENTORY_COUNT.value:
            level.last_counted_at = utcnow()

    line_costs = await costing_service.cost_posting(
        db,
        lines=engine_lines,
        levels={(level.item_id, level.warehouse_id): level for _, level in costed},
    )
    for line, level in costed:
        cost = line_costs[line.id]
        # Booked at posting time and immutable from here; the projection
        # (inventory_line_costs) carries what the line is worth as prices land.
        line.total_cost = _money(cost.total_cost)
        line.balance_after_quantity = _q(level.quantity)
        line.balance_after_value = _money(cost.running_value)
        level.projected_through_sequence = posting_sequence
        total += Decimal(str(line.total_cost))

    transaction.total_cost = _money(
        total + Decimal(str(transaction.additional_cost or 0))
    )

    # Auto off-sale (experimental): note which produced goods moved at a branch
    # that has it on, so the scheduler re-evaluates their products within a
    # tick. One upsert, committed atomically with the movement; nothing is
    # evaluated here, so an order close or a counter sale never waits on it.
    if produced_good_ids and (
        branch_settings is not None
        and branch_settings.auto_availability_enabled is True
    ):
        from app.services.inventory import auto_availability_service

        await auto_availability_service.mark_dirty(
            db,
            branch_id=transaction.branch_id,
            item_ids=produced_good_ids,
            transaction_id=transaction.id,
        )

    transaction.warehouse_id = warehouse_id
    transaction.status = TransactionStatusEnum.CLOSED.value
    transaction.poster_id = user.id if user else None
    transaction.posted_at = posted_at
    transaction.occurred_at = transaction.occurred_at or transaction.created_at

    # The database trigger rejects ad-hoc draft→closed updates. This
    # transaction-local marker says the projection and immutable snapshots were
    # prepared by this one posting path before the lifecycle changed.
    await db.execute(text("SET LOCAL mm.inventory_posting = 'on'"))
    await db.flush()
    await db.refresh(transaction)
    await db.execute(text("SET LOCAL mm.inventory_posting = 'off'"))
    return transaction


async def adjust_level(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    item_id: uuid.UUID,
    quantity_delta: Decimal,
    reason_id: uuid.UUID | None = None,
    notes: str | None = None,
    warehouse_id: uuid.UUID | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    correction_group_id: uuid.UUID | None = None,
) -> InventoryTransaction:
    """Convenience wrapper for a single-line signed quantity adjustment.

    ``source_type``/``source_id``/``correction_group_id`` let a caller attribute
    the adjustment to what caused it and group several under one id — the
    transfer-order override posts one top-up per short item, all sharing the
    order's ``adjustment_group_id`` so the mini stock-adjustment report can read
    them back as a group. Left null, this is a plain manual adjustment as before.
    ``warehouse_id`` pins the location; the poster resolves the default when null.
    """
    business_date = await business_day_service.current_business_date(db, branch)
    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Inventory item not found")

    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value
        ),
        type=InventoryTransactionTypeEnum.QUANTITY_ADJUSTMENT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        warehouse_id=warehouse_id,
        business_date=business_date,
        reason_id=reason_id,
        notes=notes,
        creator_id=user.id,
        source_type=source_type,
        source_id=source_id,
        correction_group_id=correction_group_id,
    )
    db.add(transaction)
    await db.flush()

    db.add(
        InventoryTransactionItem(
            transaction_id=transaction.id,
            item_id=item_id,
            quantity=_q(quantity_delta),
            unit="storage",
            conversion_factor=Decimal(str(item.storage_to_ingredient_factor or 1)),
            unit_cost=inventory_item_cost_for_unit(item, "storage"),
        )
    )
    await db.flush()
    await db.refresh(transaction)
    return await post_transaction(db, transaction=transaction, user=user)


# ─── Purchase-order lifecycle ─────────────────────────────────────────────────
#
# The state machine used to be spread across four endpoints in
# `api/v1/inventory.py`: each opened with its own `if purchase_order.status !=
# …: raise ConflictError(…)` and then assigned the column and stamped an actor
# by hand. Nothing named the shape of the machine, so the only way to answer
# "what can a declined order do next?" was to read four handlers and hope there
# was no fifth writer. There nearly was — `receive_purchase_order` here already
# was one, using a different guard and a different message.
#
# Same treatment as `order_lifecycle`: one map, one function that validates,
# assigns and carries the consequences. The router keeps auth and schema
# mapping and stops keeping rules.


@dataclass(frozen=True)
class _Move:
    """
    One arrival state, and what it takes to get there.

    `refusal` is a template rather than a generated sentence because these
    messages predate the extraction and reach a person in the console. A
    refactor that promises identical behaviour includes identical words, so
    each is the endpoint's own wording, moved rather than rewritten.
    """

    sources: frozenset[PurchaseOrderStatusEnum]
    refusal: str
    #: Whoever raised the order may not be the one to wave it through.
    separation_of_duties: bool = False


#: The whole machine. Statuses not named here are terminal.
#:
#: `declined` is an ending on purpose: there is no route back to `draft`,
#: because the shop's answer to a rejected order is a new one rather than a
#: quietly re-edited copy of the one somebody already refused. `closed` is the
#: other ending — stock has moved and the books have it.
PURCHASE_ORDER_MOVES: dict[PurchaseOrderStatusEnum, _Move] = {
    PurchaseOrderStatusEnum.PENDING: _Move(
        sources=frozenset({PurchaseOrderStatusEnum.DRAFT}),
        refusal="Only draft orders can be submitted (this is {status})",
    ),
    PurchaseOrderStatusEnum.APPROVED: _Move(
        sources=frozenset({PurchaseOrderStatusEnum.PENDING}),
        refusal="Only submitted orders can be approved",
        separation_of_duties=True,
    ),
    PurchaseOrderStatusEnum.DECLINED: _Move(
        sources=frozenset({PurchaseOrderStatusEnum.PENDING}),
        refusal="Only submitted orders can be declined",
    ),
    # Receiving is now one-shot: `receive_purchase_order` always closes the order
    # (whatever was short is recorded on the line), so PARTIALLY_RECEIVED is no
    # longer reached from a receive. The transition is kept so an order left
    # `partially_received` by the old multi-delivery flow can still be received to
    # CLOSED, and its status stays a legal move source below.
    PurchaseOrderStatusEnum.PARTIALLY_RECEIVED: _Move(
        sources=frozenset(
            {
                PurchaseOrderStatusEnum.APPROVED,
                PurchaseOrderStatusEnum.PARTIALLY_RECEIVED,
            }
        ),
        refusal="Purchase order is {status}; only approved orders can be received",
    ),
    PurchaseOrderStatusEnum.CLOSED: _Move(
        sources=frozenset(
            {
                PurchaseOrderStatusEnum.APPROVED,
                PurchaseOrderStatusEnum.PARTIALLY_RECEIVED,
            }
        ),
        refusal="Purchase order is {status}; only approved orders can be received",
    ),
    # Cancel a PO after the fact. Reachable from every non-terminal state:
    # voiding a received order (closed/partially_received) reverses its stock and
    # restates the weighted-average cost; voiding an un-received one (draft/
    # pending/approved) just cancels the paperwork. `declined` and `voided`
    # themselves are endings and cannot be voided again.
    PurchaseOrderStatusEnum.VOIDED: _Move(
        sources=frozenset(
            {
                PurchaseOrderStatusEnum.DRAFT,
                PurchaseOrderStatusEnum.PENDING,
                PurchaseOrderStatusEnum.APPROVED,
                PurchaseOrderStatusEnum.PARTIALLY_RECEIVED,
                PurchaseOrderStatusEnum.CLOSED,
            }
        ),
        refusal="Purchase order is {status} and cannot be voided",
    ),
}


def _po_status(value) -> PurchaseOrderStatusEnum | None:
    """The enum for a column that stores its value as a string."""
    if isinstance(value, PurchaseOrderStatusEnum):
        return value
    try:
        return PurchaseOrderStatusEnum(value)
    except ValueError:
        return None


def allowed_purchase_order_transitions(
    current: PurchaseOrderStatusEnum | str,
) -> set[PurchaseOrderStatusEnum]:
    """Everywhere a purchase order in *current* may go. Empty means terminal."""
    status = _po_status(current)
    return {
        target
        for target, move in PURCHASE_ORDER_MOVES.items()
        if status in move.sources
    }


def can_transition_purchase_order(
    current: PurchaseOrderStatusEnum | str, new: PurchaseOrderStatusEnum
) -> bool:
    """Whether the map allows moving from *current* to *new*."""
    move = PURCHASE_ORDER_MOVES.get(new)
    return move is not None and _po_status(current) in move.sources


def assert_can_transition_purchase_order(
    purchase_order: PurchaseOrder,
    new_status: PurchaseOrderStatusEnum,
    *,
    user: User | None = None,
) -> None:
    """
    Raise unless this move is legal for this order and this person.

    Public because `receive_purchase_order` has to ask *before* it does the
    work: the status it ends on is computed from what arrived, so the guard
    cannot wait for the assignment. Everything else gets this for free by
    calling `transition_purchase_order`.
    """
    move = PURCHASE_ORDER_MOVES.get(new_status)
    if move is None:
        raise ConflictError(f"'{new_status.value}' is not a state an order moves to")

    if _po_status(purchase_order.status) not in move.sources:
        raise ConflictError(move.refusal.format(status=purchase_order.status))

    # Separation of duties: whoever raised the order cannot approve their own.
    # A rule about the transition rather than about the endpoint — which is
    # what it was, and why it only ever ran on one of the ways in.
    if (
        move.separation_of_duties
        and user is not None
        and purchase_order.submitter_id == user.id
        and not user.is_admin
    ):
        raise ForbiddenError("A purchase order must be approved by someone else")


#: Set while `transition_purchase_order` is the one assigning the column. A
#: plain flag rather than the ContextVar `order_lifecycle` uses: purchase-order
#: transitions are console actions on a single row with no await between the
#: set and the reset, so there is no interleaving for two requests to confuse.
_PO_GATED = False


@event.listens_for(PurchaseOrder.status, "set", active_history=True)
def _warn_on_ungated_po_write(target: PurchaseOrder, value, oldvalue, _initiator):
    """
    A status write that did not come through `transition_purchase_order`.

    The twin of `order_lifecycle._warn_on_ungated_write`, and for the same
    reason: the machine was spread across four handlers once and the only thing
    stopping it happening again is noticing. A warning rather than a raise —
    the writers this extraction knows about all go through the gate, and the
    ones it does not know about are the ones a raise would turn from a logged
    inconsistency into a broken request. Creation writes (no previous value)
    stay free: building an order in a state is not a transition.
    """
    if _PO_GATED:
        return
    old = getattr(oldvalue, "value", oldvalue)
    new = getattr(value, "value", value)
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        return
    logger.warning(
        "PurchaseOrder.status written outside inventory_service."
        "transition_purchase_order(): %s %s -> %s. The write stands, but its "
        "validation and consequences were skipped.",
        getattr(target, "reference", target.id),
        old,
        new,
    )


async def transition_purchase_order(
    db: AsyncSession,
    purchase_order: PurchaseOrder,
    new_status: PurchaseOrderStatusEnum,
    *,
    user: User,
) -> bool:
    """
    Move a purchase order to *new_status*, with everything that move implies.

    Returns whether anything moved. `False` means it was already there — which
    is the ordinary answer for a second partial receipt, and not an error.

    No flush and no commit beyond what the consequences need: services flush,
    the request commits.
    """
    global _PO_GATED

    if purchase_order.id is not None:
        purchase_order = await _lock_purchase_order(db, purchase_order.id)

    if _po_status(purchase_order.status) == new_status:
        return False

    assert_can_transition_purchase_order(purchase_order, new_status, user=user)

    _PO_GATED = True
    try:
        purchase_order.status = new_status.value
    finally:
        _PO_GATED = False

    _purchase_order_consequences(purchase_order, new_status, user)
    return True


async def _lock_purchase_order(
    db: AsyncSession, purchase_order_id: uuid.UUID
) -> PurchaseOrder:
    purchase_order = (
        (
            await db.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.id == purchase_order_id)
                .options(
                    selectinload(PurchaseOrder.items),
                    selectinload(PurchaseOrder.misc_items),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if purchase_order is None:
        raise NotFoundError("Purchase order not found")
    return purchase_order


def _purchase_order_consequences(
    purchase_order: PurchaseOrder, new_status: PurchaseOrderStatusEnum, user: User
) -> None:
    """
    What arriving at a status makes happen, whoever brought the order there.

    Only the audit stamps, for now — the one consequence with real weight
    (stock arriving) belongs to `receive_purchase_order`, because it is
    computed from the delivery rather than implied by the status. Keyed off the
    transition all the same, so a second way to submit or approve an order
    cannot ship without the trail.

    Note the asymmetry in the declined case: `approver_id` is stamped and no
    timestamp is, because `purchase_orders` has `approved_at` and no
    `declined_at`. Preserved rather than fixed here — inventing a column is a
    migration, and this extraction promises identical behaviour.
    """
    if new_status == PurchaseOrderStatusEnum.PENDING:
        purchase_order.submitter_id = user.id
        purchase_order.submitted_at = utcnow()
    elif new_status == PurchaseOrderStatusEnum.APPROVED:
        purchase_order.approver_id = user.id
        purchase_order.approved_at = utcnow()
    elif new_status == PurchaseOrderStatusEnum.DECLINED:
        purchase_order.approver_id = user.id
    elif new_status == PurchaseOrderStatusEnum.VOIDED:
        purchase_order.voided_by = user.id
        purchase_order.voided_at = utcnow()


def normalize_item_name(name: str) -> str:
    """Fold a name for duplicate detection: lowercased, whitespace-collapsed."""
    return " ".join((name or "").split()).lower()


async def _assert_misc_names_not_inventory(db: AsyncSession, misc_lines) -> None:
    """Refuse a misc line whose name collides with an existing inventory item.

    Misc lines are deliberately outside inventory; letting one be named exactly
    like a tracked item invites someone treating the two as the same thing, so a
    normalized-name match against any non-deleted item is rejected up front.
    """
    if not misc_lines:
        return
    existing = {
        normalize_item_name(name)
        for (name,) in (
            await db.execute(
                select(InventoryItem.name).where(InventoryItem.deleted_at.is_(None))
            )
        ).all()
    }
    for line in misc_lines:
        if normalize_item_name(line.name) in existing:
            raise BadRequestError(
                f'"{line.name}" matches an existing inventory item — a '
                "miscellaneous line cannot duplicate a tracked item. Order it as "
                "a normal item, or rename the misc line."
            )


async def build_po_lines(
    db: AsyncSession,
    purchase_order: PurchaseOrder,
    lines,
    *,
    is_vat_deductible: bool,
    misc_lines=None,
    allows_misc: bool = False,
    allow_gated: bool = False,
    keep_misc: Sequence[PurchaseOrderMiscItem] = (),
) -> None:
    """Replace a PO's lines from input, splitting VAT and freezing its totals.

    Each input line carries the quantity and the VAT-inclusive line total; the
    per-unit cost (gross) and the recoverable VAT slice are derived here so both
    the admin create/edit and the till's create-and-receive price identically.

    ``misc_lines`` are free-text, non-inventory lines (only for a supplier tagged
    ``allows_misc``): they never lay a FIFO layer or move stock, but their money
    is folded into the same frozen totals so purchase history and the VAT reclaim
    (which reads the PO's totals, not its lines) include them. Each carries a
    category and period (``po_misc_service.load_categories``); an admin-only
    category needs ``allow_gated``.

    ``keep_misc`` are existing misc lines to leave in place untouched — the
    admin-only lines of an editor who cannot see them, so saving the lines they
    can see never deletes the ones they cannot. Their money is re-split with
    the current supplier's VAT setting and stays in the totals.
    """
    misc_lines = misc_lines or []
    if (misc_lines or keep_misc) and not allows_misc:
        raise BadRequestError("This supplier is not set up for miscellaneous items")
    await _assert_misc_names_not_inventory(db, misc_lines)
    already_used = {
        category_id
        for (category_id,) in (
            await db.execute(
                select(PurchaseOrderMiscItem.category_id).where(
                    PurchaseOrderMiscItem.purchase_order_id == purchase_order.id
                )
            )
        ).all()
    }
    await po_misc_service.load_categories(
        db, misc_lines, allow_gated=allow_gated, already_used=already_used
    )
    await db.execute(
        delete(PurchaseOrderItem).where(
            PurchaseOrderItem.purchase_order_id == purchase_order.id
        )
    )
    kept_ids = [line.id for line in keep_misc]
    await db.execute(
        delete(PurchaseOrderMiscItem).where(
            PurchaseOrderMiscItem.purchase_order_id == purchase_order.id,
            PurchaseOrderMiscItem.id.not_in(kept_ids),
        )
    )
    subtotal_net = Decimal("0")
    vat_total = Decimal("0")
    gross_total = Decimal("0")
    for line in lines:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        split = supplier_service.split_line_vat(
            line.entered_total, line.quantity, is_vat_deductible=is_vat_deductible
        )
        subtotal_net += split.net_total
        vat_total += split.vat_amount
        gross_total += split.total
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
                entered_total=split.total,
                vat_amount=split.vat_amount,
                net_total=split.net_total,
                unit_cost=split.unit_cost,
                total_cost=split.total,
            )
        )
    for misc in misc_lines:
        split = supplier_service.split_line_vat(
            misc.entered_total, misc.quantity, is_vat_deductible=is_vat_deductible
        )
        subtotal_net += split.net_total
        vat_total += split.vat_amount
        gross_total += split.total
        db.add(
            PurchaseOrderMiscItem(
                purchase_order_id=purchase_order.id,
                name=misc.name.strip(),
                quantity=misc.quantity,
                storage_unit=misc.storage_unit.strip(),
                entered_total=split.total,
                vat_amount=split.vat_amount,
                net_total=split.net_total,
                unit_cost=split.unit_cost,
                category_id=misc.category_id,
                period_from=misc.period_from,
                period_to=misc.period_to,
            )
        )
    for kept in keep_misc:
        split = supplier_service.split_line_vat(
            kept.entered_total, kept.quantity, is_vat_deductible=is_vat_deductible
        )
        subtotal_net += split.net_total
        vat_total += split.vat_amount
        gross_total += split.total
        kept.vat_amount = split.vat_amount
        kept.net_total = split.net_total
        kept.unit_cost = split.unit_cost
    additional = Decimal(str(purchase_order.additional_cost or 0))
    purchase_order.subtotal_net = _money(subtotal_net)
    purchase_order.vat_total = _money(vat_total)
    purchase_order.total_gross = _money(gross_total)
    purchase_order.total_cost = _money(gross_total + additional)
    await db.flush()


async def create_pos_purchase_order(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    supplier: Supplier,
    warehouse_id: uuid.UUID | None,
    data,
    invoice_object_key: str | None = None,
    invoice_content_type: str | None = None,
) -> tuple[PurchaseOrder, InventoryTransaction | None]:
    """Create a purchase order at the till and receive it in one action.

    The order is born approved (its receipt is the approval — there is no
    separate maker/checker at the counter) and immediately received in full, so
    stock and its FIFO cost land the moment the delivery is keyed in.
    """
    business_date = await business_day_service.current_business_date(db, branch)
    purchase_order = PurchaseOrder(
        reference=await next_inventory_reference(db, "PO"),
        status=PurchaseOrderStatusEnum.APPROVED.value,
        origin="pos",
        supplier_id=supplier.id,
        branch_id=branch.id,
        warehouse_id=warehouse_id,
        business_date=business_date,
        # A till PO is received the moment it is keyed, so the delivery is today
        # (the branch business date) — never a date the cashier picks.
        delivery_date=date.fromisoformat(business_date),
        supplier_reference=data.supplier_reference,
        invoice_object_key=invoice_object_key,
        invoice_content_type=invoice_content_type,
        notes=data.notes,
        creator_id=user.id,
        submitter_id=user.id,
        approver_id=user.id,
        submitted_at=utcnow(),
        approved_at=utcnow(),
    )
    db.add(purchase_order)
    await db.flush()
    await build_po_lines(
        db,
        purchase_order,
        data.items,
        is_vat_deductible=supplier.is_vat_deductible,
        misc_lines=getattr(data, "misc_items", None),
        allows_misc=supplier.allows_misc_items,
    )
    purchase_order = await _lock_purchase_order(db, purchase_order.id)
    received = {line.id: line.quantity for line in purchase_order.items}
    transaction = await receive_purchase_order(
        db, purchase_order=purchase_order, user=user, received=received
    )
    return purchase_order, transaction


async def receive_purchase_order(
    db: AsyncSession,
    *,
    purchase_order: PurchaseOrder,
    user: User,
    received: dict[uuid.UUID, Decimal],
    reasons: dict[uuid.UUID, str] | None = None,
) -> InventoryTransaction | None:
    """
    Receive an approved PO in one action, closing it.

    Returns the stock receipt, or ``None`` when nothing stocked arrived but the
    order carries miscellaneous lines — those are never inventory, so an order
    of only misc lines (a box of piping bags) closes without moving stock. Its
    money still counts: the VAT reclaim reads the closed PO's header.

    Receiving is a single event, mirroring how a transfer is received: `received`
    maps each line to the quantity that actually arrived, and whatever was not
    received is recorded as **short** (the remainder is not left outstanding for a
    later delivery — the PO closes). A line that arrived over its ordered quantity
    is an **excess**. Either way the difference is a variance, and — exactly as on
    a transfer receipt — a line whose received quantity differs from what was
    ordered must carry a `reasons[line_id]` note, or the receipt is refused.

    The one move whose consequence runs *before* the assignment: the guard is
    asked up front, against the same map, so a declined order cannot get as far as
    moving stock and then be refused.
    """
    if purchase_order.id is not None:
        purchase_order = await _lock_purchase_order(db, purchase_order.id)
    assert_can_transition_purchase_order(purchase_order, PurchaseOrderStatusEnum.CLOSED)

    branch = await db.get(Branch, purchase_order.branch_id)
    if branch is None:
        raise NotFoundError("Branch not found")
    business_date = await business_day_service.current_business_date(db, branch)

    reasons = reasons or {}

    # A discrepant line needs a reason before anything moves — collected up front
    # so the message names every one at once rather than failing line by line.
    missing: list[str] = []
    for po_item in purchase_order.items:
        quantity = _q(received.get(po_item.id, 0))
        if quantity != _q(po_item.quantity):
            reason = reasons.get(po_item.id)
            if not reason or not reason.strip():
                item = await db.get(InventoryItem, po_item.item_id)
                missing.append(getattr(item, "name", None) or str(po_item.item_id))
    if missing:
        raise BadRequestError(
            "A reason is required where the received quantity differs from what was "
            "ordered: " + ", ".join(missing)
        )

    lines: list[InventoryTransactionItem] = []
    paid_tax = Decimal("0")
    for po_item in purchase_order.items:
        quantity = _q(received.get(po_item.id, 0))
        # Record what arrived and the variance note (one-shot receipt: this is the
        # final received quantity, whether short, exact or over).
        po_item.received_quantity = quantity
        ordered = Decimal(str(po_item.quantity or 0))
        po_item.variance_reason = (
            reasons.get(po_item.id, "").strip() or None
            if quantity != _q(ordered)
            else None
        )
        if quantity <= 0:
            continue
        lines.append(
            InventoryTransactionItem(
                item_id=po_item.item_id,
                quantity=quantity,
                unit=po_item.unit,
                conversion_factor=po_item.conversion_factor,
                unit_cost=po_item.unit_cost,
            )
        )
        # Carry the recoverable VAT for the portion received, pro rata, onto the
        # ledger transaction so the reclaim report can read it (the cost itself
        # is gross and lands in the FIFO layer via unit_cost above). The invoice's
        # VAT is fixed at the ordered quantity, so an over-receipt is capped at the
        # ordered amount — receiving extra stock does not reclaim extra VAT.
        if ordered > 0:
            reclaimable = min(quantity, ordered)
            paid_tax += _money(
                Decimal(str(po_item.vat_amount or 0)) * (reclaimable / ordered)
            )

    if not lines:
        if not purchase_order.misc_items:
            raise BadRequestError("Nothing was received")
        # Only non-inventory lines arrived: close the order, move no stock.
        await transition_purchase_order(
            db, purchase_order, PurchaseOrderStatusEnum.CLOSED, user=user
        )
        await db.flush()
        return None

    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.PURCHASING.value
        ),
        type=InventoryTransactionTypeEnum.PURCHASING.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=purchase_order.branch_id,
        warehouse_id=purchase_order.warehouse_id,
        supplier_id=purchase_order.supplier_id,
        purchase_order_id=purchase_order.id,
        business_date=business_date,
        creator_id=user.id,
        paid_tax=_money(paid_tax),
        # Built with its lines, so the append can't trigger a lazy load of the
        # flushed transaction's collection (MissingGreenlet under async).
        items=lines,
    )
    db.add(transaction)
    await db.flush()
    await db.refresh(transaction)
    posted = await post_transaction(db, transaction=transaction, user=user)

    # One-shot: the receipt closes the order, whatever was short.
    await transition_purchase_order(
        db, purchase_order, PurchaseOrderStatusEnum.CLOSED, user=user
    )
    await db.flush()
    return posted


# ─── Depletion from sales ─────────────────────────────────────────────────────


async def deplete_for_order(
    db: AsyncSession, *, order: Order, user: User
) -> InventoryTransaction | None:
    """
    Freeze and consume inventory for a finalized order in MM acceptance order.

    The durable source event owns idempotency and recipe history. A missing
    recipe is recorded as an exception without blocking the sale.
    """
    from app.services.inventory import source_event_service

    event_row = await source_event_service.accept_order(db, order=order, user=user)
    if event_row is None or event_row.transaction_id is None:
        return None
    return await db.get(InventoryTransaction, event_row.transaction_id)


async def restock_for_void(db: AsyncSession, *, order: Order, user: User) -> None:
    """
    Put a voided order's consumed ingredients back on the shelf.

    A counter sale posts a `CONSUMPTION_FROM_ORDERS` movement when it closes;
    voiding it after the fact reverses that movement in full. Delegates to the
    same return machinery the admin inventory-return endpoint uses, at a full
    `restock` disposition — which also resolves the disposition-required
    exception the cancellation logged against the frozen consumption. A no-op
    for an order that never consumed anything (inventory off, or no recipes),
    because `record_return` finds no original movement to reverse — and a no-op if
    the consumption was already reversed (e.g. a pre-packing cancellation beat the
    void here), since the FIFO return cap would otherwise raise on a second full
    return.
    """
    from app.services.inventory import source_event_service

    already_returned = await db.scalar(
        select(InventoryTransaction.id).where(
            InventoryTransaction.order_id == order.id,
            InventoryTransaction.type
            == InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value,
            InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
        )
    )
    if already_returned is not None:
        return
    await source_event_service.record_return(
        db,
        order=order,
        user=user,
        disposition="restock",
        proportion=Decimal("1"),
        idempotency_key=f"void:{order.id}",
        notes="Counter sale voided",
    )


async def load_transaction(
    db: AsyncSession, transaction_id: uuid.UUID
) -> InventoryTransaction:
    stmt = (
        select(InventoryTransaction)
        .where(InventoryTransaction.id == transaction_id)
        .options(selectinload(InventoryTransaction.items))
    )
    transaction = (await db.execute(stmt)).scalars().unique().one_or_none()
    if transaction is None:
        raise NotFoundError("Inventory transaction not found")
    return transaction


async def record_waste(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    item_id: uuid.UUID,
    quantity: Decimal,
    from_production: bool = False,
    reason_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> InventoryTransaction:
    """
    Write off stock that was thrown away.

    Kept distinct from a quantity adjustment even though both reduce stock: a
    correction means the count was wrong, waste means the food is in the bin.
    Only the second belongs in a wastage cost report, so conflating them would
    hide the number a kitchen is actually managed on.
    """
    if quantity <= 0:
        raise BadRequestError("Waste quantity must be positive")

    kind = (
        InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION
        if from_production
        else InventoryTransactionTypeEnum.WASTE_FROM_ORDERS
    )
    business_date = await business_day_service.current_business_date(db, branch)
    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Inventory item not found")

    transaction = InventoryTransaction(
        reference=await next_reference(db, kind.value),
        type=kind.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        business_date=business_date,
        reason_id=reason_id,
        notes=notes,
        creator_id=user.id,
    )
    db.add(transaction)
    await db.flush()

    db.add(
        InventoryTransactionItem(
            transaction_id=transaction.id,
            item_id=item_id,
            # Positive: the waste transaction type already carries sign -1,
            # so negating here as well would put stock back on the shelf.
            quantity=_q(abs(quantity)),
            unit="storage",
            conversion_factor=Decimal(str(item.storage_to_ingredient_factor or 1)),
            unit_cost=inventory_item_cost_for_unit(item, "storage"),
        )
    )
    await db.flush()
    await db.refresh(transaction)
    return await post_transaction(db, transaction=transaction, user=user)


async def adjust_cost(
    db: AsyncSession,
    *,
    branch: Branch,
    item_id: uuid.UUID,
    warehouse_id: uuid.UUID | None,
    new_average_cost: Decimal,
    user: User | None = None,
    notes: str | None = None,
) -> dict:
    """
    Restate what stock on hand is worth, without moving any of it.

    A supplier reprices, or a cost was keyed wrong, and the valuation is now
    misleading. Quantity is deliberately untouched — this only moves money,
    and the previous cost is returned so the caller can record the delta.
    """
    if new_average_cost < 0:
        raise BadRequestError("Cost cannot be negative")
    branch_id = branch.id
    business_date = await business_day_service.current_business_date(db, branch)

    stmt = select(InventoryLevel).where(InventoryLevel.item_id == item_id)
    if warehouse_id is not None:
        await assert_warehouse_for_branch(db, warehouse_id, branch_id)
        stmt = stmt.where(InventoryLevel.warehouse_id == warehouse_id)
    else:
        warehouse_id = (await default_warehouse(db, branch_id)).id
        stmt = stmt.where(InventoryLevel.warehouse_id == warehouse_id)
    level = (await db.execute(stmt)).scalars().first()
    if level is None:
        raise NotFoundError("This item has no stock level to revalue")

    previous = Decimal(str(level.average_cost or 0))
    quantity = Decimal(str(level.quantity or 0))
    # Booked as a transaction, not just a field write. Its type carries sign 0
    # so posting it cannot move stock, but it leaves the trail the cost
    # adjustment report reads — a revaluation that only edits a column is
    # invisible the moment anyone asks why the valuation changed.
    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.COST_ADJUSTMENT.value
        ),
        type=InventoryTransactionTypeEnum.COST_ADJUSTMENT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch_id,
        warehouse_id=level.warehouse_id,
        business_date=business_date,
        notes=notes,
        creator_id=user.id if user else None,
        # Seed the collection so the line append does not trigger an implicit
        # selectin load on the flushed transaction (MissingGreenlet under async).
        items=[
            InventoryTransactionItem(
                item_id=item_id,
                quantity=_q(quantity),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=_c(new_average_cost),
            )
        ],
    )
    db.add(transaction)
    await db.flush()
    await post_transaction(db, transaction=transaction, user=user)

    return {
        "item_id": str(item_id),
        "warehouse_id": str(level.warehouse_id) if level.warehouse_id else None,
        "quantity_on_hand": quantity,
        "previous_average_cost": previous,
        "new_average_cost": new_average_cost,
        # What the revaluation did to the books, which is the point of it.
        "value_change": (new_average_cost - previous) * quantity,
        "adjusted_by": (user.display_name or user.email) if user else "system",
        "notes": notes,
    }


async def restate_production_cost(
    db: AsyncSession,
    *,
    production_transaction_id: uuid.UUID,
    unit_cost: Decimal,
    user: User | None = None,
    notes: str | None = None,
) -> InventoryTransaction:
    """
    Re-cost one production batch at *unit_cost* per storage unit, after the fact.

    A batch is costed at what its recorded inputs cost, so one made from an
    incomplete recipe — an ingredient left out, or one not priced yet — carries
    too little (or, with an over-stated line, too much). Its stock has usually
    long since been transferred and sold. This books the correction in the
    ledger: a ``production_restatement`` linked to the batch by its
    ``correction_group_id``, which the costing engine applies from the batch's
    own posting on, so every transfer, sale and batch that drew from it is
    re-priced with it. No stock moves; reversing it restores the recorded cost.
    """
    if unit_cost < 0:
        raise BadRequestError("Cost cannot be negative")
    production = await load_transaction(db, production_transaction_id)
    if (
        production.type != InventoryTransactionTypeEnum.PRODUCTION.value
        or not production.is_posted
        or production.reverses_transaction_id is not None
    ):
        raise BadRequestError("Only a posted production batch can be restated")
    if production.correction_group_id is None or len(production.items) != 1:
        raise BadRequestError(
            f"{production.reference} is not a single-item batch and cannot be restated"
        )
    line = production.items[0]
    branch = await db.get(Branch, production.branch_id)
    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.PRODUCTION_RESTATEMENT.value
        ),
        type=InventoryTransactionTypeEnum.PRODUCTION_RESTATEMENT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=production.branch_id,
        warehouse_id=production.warehouse_id,
        business_date=await business_day_service.current_business_date(db, branch),
        correction_group_id=production.correction_group_id,
        source_type="production",
        source_id=str(production.id),
        notes=notes,
        creator_id=user.id if user else None,
        # Seeded in the constructor: appending after a flush would lazy-load the
        # collection (MissingGreenlet under async).
        items=[
            InventoryTransactionItem(
                item_id=line.item_id,
                quantity=_q(Decimal(str(line.signed_quantity))),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=_c(unit_cost),
            )
        ],
    )
    db.add(transaction)
    await db.flush()
    return await post_transaction(db, transaction=transaction, user=user)


async def open_count(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    warehouse_id: uuid.UUID | None,
    item_ids: list[uuid.UUID] | None,
    notes: str | None = None,
) -> InventoryTransaction:
    """
    Start a stock count and freeze what the system currently believes.

    The system quantity is captured now, not at close, because a count can
    take an hour and sales keep happening. Comparing tonight's count against
    a figure that moved while counting would manufacture a variance nobody
    can explain.
    """
    warehouse = (
        (await assert_warehouse_for_branch(db, warehouse_id, branch.id)).id
        if warehouse_id
        else (await default_warehouse(db, branch.id)).id
    )
    transaction = InventoryTransaction(
        reference=await next_reference(
            db, InventoryTransactionTypeEnum.INVENTORY_COUNT.value
        ),
        type=InventoryTransactionTypeEnum.INVENTORY_COUNT.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        warehouse_id=warehouse,
        business_date=await business_day_service.current_business_date(db, branch),
        notes=notes,
        creator_id=user.id,
    )
    db.add(transaction)
    await db.flush()

    stmt = select(InventoryLevel).where(InventoryLevel.warehouse_id == warehouse)
    if item_ids:
        stmt = stmt.where(InventoryLevel.item_id.in_(item_ids))
    for level in (await db.execute(stmt)).scalars().all():
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=level.item_id,
                # Zero until counted; the frozen system figure rides along as
                # the unit cost slot's sibling on the level itself.
                quantity=Decimal("0"),
                unit="storage",
                conversion_factor=Decimal("1"),
                unit_cost=_c(level.average_cost),
                expected_quantity=_q(level.quantity),
            )
        )
    await db.flush()
    await db.refresh(transaction)
    return transaction


async def close_count(
    db: AsyncSession,
    *,
    transaction: InventoryTransaction,
    user: User,
    counted: dict[uuid.UUID, Decimal],
) -> dict:
    """
    Post a count: write the variance to stock and report it line by line.

    The movement posted is the difference, not the counted figure — posting
    the count itself would add a second copy of everything on the shelf.
    """
    if transaction.type != InventoryTransactionTypeEnum.INVENTORY_COUNT.value:
        raise BadRequestError("That transaction is not a stock count")
    if transaction.status != TransactionStatusEnum.DRAFT.value:
        raise ConflictError("This count is already closed")

    # Write the counted balance, not the variance. post_transaction already
    # treats a count line as "what is actually on the shelf" and works out the
    # movement itself — computing the difference here too subtracted it twice
    # and left the stock sitting at the variance.
    from app.services.inventory import source_event_service

    await source_event_service.lock_branch_inventory(db, transaction.branch_id)
    systems, costs = {}, {}
    for line in transaction.items:
        level = await level_for(
            db, line.item_id, transaction.warehouse_id, for_update=True
        )
        systems[line.item_id] = Decimal(str(level.quantity or 0))
        costs[line.item_id] = Decimal(str(level.average_cost or 0))
        line.quantity = _q(
            Decimal(str(counted.get(line.item_id, systems[line.item_id])))
        )

    await db.flush()
    await db.refresh(transaction)
    posted = await post_transaction(db, transaction=transaction, user=user)

    lines, total_value = [], Decimal("0")
    for line in posted.items:
        system = systems.get(line.item_id, Decimal("0"))
        actual = Decimal(str(line.quantity))
        variance = actual - system
        value = variance * costs.get(line.item_id, Decimal("0"))
        total_value += value
        lines.append(
            {
                "item_id": str(line.item_id),
                "system_quantity": _q(system),
                "counted_quantity": _q(actual),
                "variance": _q(variance),
                "value_change": _money(value),
            }
        )
    return {
        "reference": posted.reference,
        "status": posted.status,
        "lines": lines,
        "total_value_change": _money(total_value),
    }
