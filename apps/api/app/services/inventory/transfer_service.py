"""
Inter-branch transfers and production.

**Transfers** separate the *request* from the *movement*, exactly as Foodics
does. A transfer order is raised by the receiving branch, accepted (possibly for
a reduced quantity) by the source, then sent and received. Only sending and
receiving touch stock, which is what lets a branch dispute a short delivery
without the books already having moved.

Stock in flight is deliberately visible: sending decrements the source
immediately, receiving increments the destination. The gap between the two is
real — the goods are on a van.

**Production** turns ingredients into a finished item. It is two linked
transactions: a production receipt for the output and a consumption issue for
the inputs, so cost flows from one to the other rather than appearing from
nowhere.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError

# Aliased to the existing private names: the implementation is shared,
# the call sites stay put, and `quantity` is already a local variable in
# both of these files.
from app.core.money import quantity as _q
from app.core.money import unit_cost as _c
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryItemIngredient,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.models.inventory_v2 import BranchInventorySettings
from app.models.operations import (
    InventoryTransferTemplate,
    Transfer,
    TransferKindEnum,
    TransferLine,
    TransferOrder,
    TransferOrderStatusEnum,
    TransferStatusEnum,
)
from app.models.user import User
from app.services.inventory import (
    inventory_service,
    recipe_service,
    source_event_service,
    transfer_template_service,
)
from app.services.pos import business_day_service

__all__ = [
    "production_output",
    "production_unit_cost",
    "create_return_order",
    "create_transfer_order",
    "load_transfer",
    "load_transfer_order",
    "mark_transfer_sent",
    "produce",
    "receive_transfer",
]


def production_output(quantity: Decimal, yield_percentage: Decimal) -> Decimal:
    """
    How much actually comes out of a batch.

    A recipe with a 0.9 yield loses 10% in the process, so a 100-unit batch
    produces 90. Modelling the loss on the output (rather than inflating the
    inputs) keeps the recipe readable and makes the waste visible in cost.
    """
    factor = Decimal(str(yield_percentage or 1))
    if factor <= 0:
        factor = Decimal("1")
    return _q(Decimal(str(quantity)) * factor)


def production_unit_cost(input_cost: Decimal, net_output: Decimal) -> Decimal:
    """
    Cost of one produced unit: the whole input cost spread over what survived.

    Because yield loss shrinks the denominator, a wasteful recipe correctly
    reports a *higher* cost per unit rather than silently losing the value.
    """
    if net_output <= 0:
        return _c(0)
    return _c(Decimal(str(input_cost)) / Decimal(str(net_output)))


async def next_transfer_order_reference(db: AsyncSession) -> str:
    """A parent order reference (TO-…)."""
    return await inventory_service.next_inventory_reference(db, "TO")


async def next_transfer_reference(db: AsyncSession) -> str:
    """A child transfer reference (TRF-…). One per destination branch."""
    return await inventory_service.next_inventory_reference(db, "TRF")


async def load_transfer_order(db: AsyncSession, order_id: uuid.UUID) -> TransferOrder:
    """A parent order with its children and their lines."""
    order = (
        (
            await db.execute(
                select(TransferOrder)
                .where(TransferOrder.id == order_id)
                .options(
                    selectinload(TransferOrder.children).selectinload(Transfer.items)
                )
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if order is None:
        raise NotFoundError("Transfer order not found")
    return order


async def load_transfer(db: AsyncSession, transfer_id: uuid.UUID) -> Transfer:
    """One child transfer with its lines."""
    transfer = (
        (
            await db.execute(
                select(Transfer)
                .where(Transfer.id == transfer_id)
                .options(selectinload(Transfer.items))
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if transfer is None:
        raise NotFoundError("Transfer not found")
    return transfer


async def _lock_transfer(db: AsyncSession, transfer_id: uuid.UUID) -> Transfer:
    """Serialize every state transition and stock movement for one child."""
    transfer = (
        (
            await db.execute(
                select(Transfer)
                .where(Transfer.id == transfer_id)
                .options(selectinload(Transfer.items))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if transfer is None:
        raise NotFoundError("Transfer not found")
    return transfer


# ─── Create (admin fan-out) ────────────────────────────────────────────────────
#
# An admin raises one order from a single source branch, allocating quantities to
# several destinations at once; it fans out into one child `Transfer` per
# destination. Nothing moves at create time — each child is shipped from the
# source till later (`mark_transfer_sent`), one at a time, and received at the
# destination (`receive_transfer`). Movement lives in the child's two link
# columns, not its status; the parent status is derived from its children.


async def _recompute_parent_status(db: AsyncSession, order_id: uuid.UUID) -> None:
    """Roll the children's statuses up onto the parent, under a parent lock so two
    children transitioning at once cannot race to a wrong answer."""
    order = (
        await db.execute(
            select(TransferOrder)
            .where(TransferOrder.id == order_id)
            .options(selectinload(TransferOrder.children))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if order is None:
        return
    live = [c for c in order.children if c.status != TransferStatusEnum.CANCELLED.value]
    S = TransferStatusEnum
    P = TransferOrderStatusEnum
    if not live:
        order.status = P.CANCELLED.value
    elif all(c.status == S.CLOSED.value for c in live):
        order.status = P.CLOSED.value
    elif any(c.status == S.CLOSED.value for c in live):
        order.status = P.PARTIALLY_RECEIVED.value
    elif all(c.status == S.SENT.value for c in live):
        order.status = P.SENT.value
    elif any(c.status == S.SENT.value for c in live):
        order.status = P.PARTIALLY_SENT.value
    else:
        order.status = P.PENDING.value
    await db.flush()


async def create_transfer_order(
    db: AsyncSession,
    *,
    source_branch: Branch,
    user: User,
    items: list,
    kind: str = TransferKindEnum.TRANSFER.value,
    notes: str | None = None,
    required_date=None,
    template_id: uuid.UUID | None = None,
    client_request_id: str | None = None,
) -> TransferOrder:
    """Raise a parent order from one source branch, fanning out to many.

    ``items`` is a list of per-item allocations, each carrying ``item_id``,
    ``unit``, an ``override`` flag and ``allocations`` — a list of
    ``(branch_id, quantity)`` for the destinations this item goes to. One child
    ``Transfer`` is created per destination that receives a nonzero quantity of
    any item, each with its own lines. **No stock moves here.**

    When the total of an item across all destinations exceeds the source's
    on-hand, the admin must set ``override=True`` on that item: a shortfall
    top-up ``QUANTITY_ADJUSTMENT`` is posted to the source at create time
    (grouped under the order's ``adjustment_group_id``, so the mini
    stock-adjustment report can read them back), raising on-hand to exactly cover
    the fan-out so no later send goes negative. Without the flag the create is
    refused, naming the shortfall.
    """
    if not items:
        raise BadRequestError("A transfer order needs at least one line")

    if client_request_id:
        existing = (
            await db.execute(
                select(TransferOrder).where(
                    TransferOrder.client_request_id == client_request_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return await load_transfer_order(db, existing.id)

    template_version: int | None = None
    template_snapshot: dict | None = None
    if template_id is not None:
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
            .one_or_none()
        )
        if template is None:
            raise BadRequestError(f"Transfer template {template_id} not found")
        template_version = template.version_number
        template_snapshot = await transfer_template_service.snapshot_template(
            db, template
        )

    # Read on-hand and issue any override top-up under the source's inventory
    # lock — the same lock the sends will take — so the on-hand the override
    # corrects is the on-hand the sends draw down.
    await source_event_service.lock_branch_inventory(db, source_branch.id)
    source_warehouse = await inventory_service.default_warehouse(db, source_branch.id)

    resolved: list[dict] = []
    for entry in items:
        item = await db.get(InventoryItem, entry.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {entry.item_id} not found")
        unit = getattr(entry, "unit", "storage")
        factor = (
            Decimal("1")
            if unit == "ingredient"
            else Decimal(str(item.storage_to_ingredient_factor))
        )
        allocations = [
            (a.branch_id, _q(a.quantity))
            for a in entry.allocations
            if _q(a.quantity) > 0
        ]
        if not allocations:
            continue
        if any(branch_id == source_branch.id for branch_id, _ in allocations):
            raise BadRequestError("A branch cannot transfer to itself")
        total_ingredient = sum((qty * factor for _b, qty in allocations), Decimal("0"))
        resolved.append(
            {
                "item": item,
                "unit": unit,
                "factor": factor,
                "allocations": allocations,
                "override": bool(getattr(entry, "override", False)),
                "total_ingredient": total_ingredient,
            }
        )

    if not resolved:
        raise BadRequestError("A transfer order needs at least one line")

    order = TransferOrder(
        reference=await next_transfer_order_reference(db),
        kind=kind,
        status=TransferOrderStatusEnum.PENDING.value,
        source_branch_id=source_branch.id,
        source_warehouse_id=source_warehouse.id,
        business_date=await business_day_service.current_business_date(
            db, source_branch
        ),
        required_date=required_date,
        notes=notes,
        client_request_id=client_request_id,
        creator_id=user.id,
        template_id=template_id,
        template_version=template_version,
        template_snapshot=template_snapshot,
    )
    try:
        async with db.begin_nested():
            db.add(order)
            await db.flush()
    except IntegrityError:
        if client_request_id:
            existing = (
                await db.execute(
                    select(TransferOrder).where(
                        TransferOrder.client_request_id == client_request_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return await load_transfer_order(db, existing.id)
        raise

    # Override top-ups first, so a later send cannot trip prevent-negative.
    adjustment_group: uuid.UUID | None = None
    for row in resolved:
        item = row["item"]
        level = await inventory_service.level_for(db, item.id, source_warehouse.id)
        on_hand = _q(level.quantity)
        shortfall = _q(row["total_ingredient"] - on_hand)
        if shortfall <= 0:
            continue
        if not row["override"]:
            raise BadRequestError(
                f"{item.name}: transferring {row['total_ingredient']} "
                f"{item.ingredient_unit} but only {on_hand} on hand. "
                "Set override to adjust stock and proceed."
            )
        if adjustment_group is None:
            adjustment_group = uuid.uuid4()
            order.adjustment_group_id = adjustment_group
        await inventory_service.adjust_level(
            db,
            branch=source_branch,
            user=user,
            item_id=item.id,
            quantity_delta=shortfall,
            warehouse_id=source_warehouse.id,
            source_type="transfer_shortfall_adjustment",
            source_id=str(order.id),
            correction_group_id=adjustment_group,
            notes=f"Shortfall top-up for {order.reference}",
        )

    # One child per destination branch, with that branch's lines.
    by_branch: dict[uuid.UUID, list[dict]] = {}
    for row in resolved:
        for branch_id, qty in row["allocations"]:
            by_branch.setdefault(branch_id, []).append({"row": row, "qty": qty})

    for branch_id, lines in by_branch.items():
        child = Transfer(
            transfer_order_id=order.id,
            reference=await next_transfer_reference(db),
            kind=kind,
            status=TransferStatusEnum.PENDING.value,
            branch_id=branch_id,
            source_branch_id=source_branch.id,
            source_warehouse_id=source_warehouse.id,
            business_date=order.business_date,
            creator_id=user.id,
        )
        db.add(child)
        await db.flush()
        for line in lines:
            row = line["row"]
            db.add(
                TransferLine(
                    transfer_id=child.id,
                    item_id=row["item"].id,
                    quantity=line["qty"],
                    approved_quantity=line["qty"],
                    unit=row["unit"],
                    conversion_factor=row["factor"],
                )
            )
    await db.flush()
    return await load_transfer_order(db, order.id)


async def create_return_order(
    db: AsyncSession,
    *,
    source_branch: Branch,
    destination_branch: Branch,
    user: User,
    lines: list,
    notes: str | None = None,
    client_request_id: str | None = None,
) -> TransferOrder:
    """A return is a single-child order (source → its return branch) that ships
    immediately: the till packs a box of goods going back and sends it, so the
    one child is created, marked sent, and — when the destination runs no POS —
    received straight away. It reuses the whole transfer machinery, differing only
    in kind and that each line carries a reason.
    """
    if not lines:
        raise BadRequestError("A return needs at least one line")
    if source_branch.id == destination_branch.id:
        raise BadRequestError("A branch cannot return to itself")

    if client_request_id:
        existing = (
            await db.execute(
                select(TransferOrder).where(
                    TransferOrder.client_request_id == client_request_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return await load_transfer_order(db, existing.id)

    source_warehouse = await inventory_service.default_warehouse(db, source_branch.id)
    order = TransferOrder(
        reference=await next_transfer_order_reference(db),
        kind=TransferKindEnum.RETURN.value,
        status=TransferOrderStatusEnum.PENDING.value,
        source_branch_id=source_branch.id,
        source_warehouse_id=source_warehouse.id,
        business_date=await business_day_service.current_business_date(
            db, source_branch
        ),
        notes=notes,
        client_request_id=client_request_id,
        creator_id=user.id,
    )
    try:
        async with db.begin_nested():
            db.add(order)
            await db.flush()
    except IntegrityError:
        if client_request_id:
            existing = (
                await db.execute(
                    select(TransferOrder).where(
                        TransferOrder.client_request_id == client_request_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return await load_transfer_order(db, existing.id)
        raise

    child = Transfer(
        transfer_order_id=order.id,
        reference=await next_transfer_reference(db),
        kind=TransferKindEnum.RETURN.value,
        status=TransferStatusEnum.PENDING.value,
        branch_id=destination_branch.id,
        source_branch_id=source_branch.id,
        source_warehouse_id=source_warehouse.id,
        business_date=order.business_date,
        creator_id=user.id,
    )
    db.add(child)
    await db.flush()
    for line in lines:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        unit = getattr(line, "unit", "storage")
        db.add(
            TransferLine(
                transfer_id=child.id,
                item_id=line.item_id,
                quantity=_q(line.quantity),
                approved_quantity=_q(line.quantity),
                unit=unit,
                conversion_factor=(
                    Decimal("1")
                    if unit == "ingredient"
                    else Decimal(str(item.storage_to_ingredient_factor))
                ),
                variance_reason=getattr(line, "variance_reason", None),
            )
        )
    await db.flush()

    child = await load_transfer(db, child.id)
    await mark_transfer_sent(db, transfer=child, user=user)
    if not getattr(destination_branch, "uses_pos", True):
        child = await load_transfer(db, child.id)
        await receive_transfer(db, transfer=child, user=user)
    return await load_transfer_order(db, order.id)


# ─── Movement ─────────────────────────────────────────────────────────────────


async def mark_transfer_sent(
    db: AsyncSession,
    *,
    transfer: Transfer,
    user: User,
    sent: dict[uuid.UUID, Decimal] | None = None,
) -> InventoryTransaction:
    """Ship one child from the source, decrementing its stock. Idempotent: a
    second call returns the transaction already posted.

    ``sent`` optionally overrides how much of each line actually leaves, keyed by
    ``TransferLine.id``. A line absent from the map ships its requested quantity,
    so a bodyless send is unchanged. A line whose sent quantity differs from its
    requested ``quantity`` is a *sending variance*, recorded on ``sent_quantity``
    (the requested ``quantity`` is left untouched as the baseline)."""
    transfer = await _lock_transfer(db, transfer.id)
    if transfer.sent_transaction_id is not None:
        return await inventory_service.load_transaction(
            db, transfer.sent_transaction_id
        )
    if transfer.status == TransferStatusEnum.CANCELLED.value:
        raise ConflictError("This transfer was cancelled")

    source = await db.get(Branch, transfer.source_branch_id)
    if source is None:
        raise NotFoundError("Source branch not found")
    # The source moving average is part of the immutable transfer snapshot, so it
    # must be read under the same branch lock that will issue the stock.
    await source_event_service.lock_branch_inventory(db, transfer.source_branch_id)
    source_warehouse = (
        await inventory_service.assert_warehouse_for_branch(
            db, transfer.source_warehouse_id, transfer.source_branch_id
        )
        if transfer.source_warehouse_id is not None
        else await inventory_service.default_warehouse(db, transfer.source_branch_id)
    )

    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.TRANSFER_SEND.value
        ),
        type=InventoryTransactionTypeEnum.TRANSFER_SEND.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=transfer.source_branch_id,
        warehouse_id=source_warehouse.id,
        other_branch_id=transfer.branch_id,
        other_warehouse_id=transfer.warehouse_id,
        business_date=await business_day_service.current_business_date(db, source),
        creator_id=user.id,
        idempotency_key=f"transfer:{transfer.id}:send",
        source_type="transfer",
        source_id=str(transfer.id),
        notes=f"Transfer {transfer.reference}",
    )
    db.add(transaction)
    await db.flush()

    for line in transfer.items:
        if sent is not None and line.id in sent:
            quantity = _q(sent[line.id])
        else:
            quantity = _q(
                line.approved_quantity
                if line.approved_quantity is not None
                else line.quantity
            )
        # Record what left even when it is zero, so a line the picker dropped
        # reads as a shipped 0 (a variance against its request) rather than the
        # requested amount. Only a positive quantity moves stock.
        line.sent_quantity = quantity
        if quantity <= 0:
            continue
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        level = await inventory_service.level_for(db, line.item_id, source_warehouse.id)
        ingredient_cost = _c(level.average_cost)
        if ingredient_cost == 0:
            ingredient_cost = inventory_service.inventory_item_cost_for_unit(
                item, "ingredient"
            )
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=line.item_id,
                quantity=quantity,
                unit=line.unit,
                conversion_factor=line.conversion_factor,
                unit_cost=inventory_service.ingredient_cost_for_unit(
                    item, ingredient_cost, line.unit
                ),
                notes=f"transfer_item:{line.id}",
            )
        )

    await db.flush()
    transaction = await inventory_service.load_transaction(db, transaction.id)
    posted = await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )
    transfer.sent_transaction_id = posted.id
    transfer.status = TransferStatusEnum.SENT.value
    await db.flush()
    await _recompute_parent_status(db, transfer.transfer_order_id)

    # A destination that runs no POS (DSO, Karama) has no till to receive on:
    # book the shipment straight in so its on-hand is right and it is not left
    # forever "to receive" at a branch that cannot act on it.
    destination = await db.get(Branch, transfer.branch_id)
    if destination is not None and not getattr(destination, "uses_pos", True):
        fresh = await load_transfer(db, transfer.id)
        await receive_transfer(db, transfer=fresh, user=user)
    return posted


async def receive_transfer(
    db: AsyncSession,
    *,
    transfer: Transfer,
    user: User,
    received: dict[uuid.UUID, Decimal] | None = None,
    reasons: dict[uuid.UUID, str] | None = None,
) -> InventoryTransaction:
    """
    Book a shipped child into the destination — what actually arrived, which may
    be less **or more** than was sent.

    Same two-leg cost-carry as before: the sent leg's snapshot cost is read back
    off the ``transfer_item:{line.id}`` note; a short receipt leaves the shortfall
    on the sender's books (a transit loss they own), an over-receipt is a gain at
    the destination, both recorded on the line with the receiver's ``reason``.
    Posting a separate shrinkage/found movement here would double-count, because
    the send already moved the stock.
    """
    transfer = await _lock_transfer(db, transfer.id)
    if transfer.received_transaction_id is not None:
        return await inventory_service.load_transaction(
            db, transfer.received_transaction_id
        )
    if transfer.sent_transaction_id is None:
        raise ConflictError("This transfer has not been sent yet")

    destination = await db.get(Branch, transfer.branch_id)
    if destination is None:
        raise NotFoundError("Destination branch not found")
    sent_transaction = await inventory_service.load_transaction(
        db, transfer.sent_transaction_id
    )
    sent_costs = {
        sent_line.notes: _c(sent_line.unit_cost)
        for sent_line in sent_transaction.items
        if sent_line.notes and sent_line.notes.startswith("transfer_item:")
    }

    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value
        ),
        type=InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=transfer.branch_id,
        warehouse_id=transfer.warehouse_id,
        other_branch_id=transfer.source_branch_id,
        other_warehouse_id=transfer.source_warehouse_id,
        business_date=await business_day_service.current_business_date(db, destination),
        creator_id=user.id,
        idempotency_key=f"transfer:{transfer.id}:receive",
        source_type="transfer",
        source_id=str(transfer.id),
        notes=f"Transfer {transfer.reference}",
    )
    db.add(transaction)
    await db.flush()

    for line in transfer.items:
        sent = _q(line.sent_quantity)
        quantity = _q(received.get(line.id, sent)) if received else sent
        if reasons is not None and line.id in reasons:
            line.variance_reason = reasons[line.id] or None
        line.received_quantity = quantity if quantity > 0 else _q(0)
        if quantity <= 0:
            continue
        sent_cost = sent_costs.get(f"transfer_item:{line.id}")
        if sent_cost is None:
            if _q(line.sent_quantity) <= 0:
                raise BadRequestError(
                    "This line had nothing sent, so it cannot be received. "
                    "Record found stock as a count instead."
                )
            raise ConflictError(
                "The sent transfer is missing its immutable line cost snapshot"
            )
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=line.item_id,
                quantity=quantity,
                unit=line.unit,
                conversion_factor=line.conversion_factor,
                unit_cost=sent_cost,
                notes=f"transfer_item:{line.id}",
            )
        )

    await db.flush()
    transaction = await inventory_service.load_transaction(db, transaction.id)
    posted = await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )
    transfer.received_transaction_id = posted.id
    transfer.status = TransferStatusEnum.CLOSED.value
    await db.flush()
    await _recompute_parent_status(db, transfer.transfer_order_id)
    return posted


# ─── Production ───────────────────────────────────────────────────────────────


async def produce(
    db: AsyncSession,
    *,
    branch: Branch,
    user: User,
    item_id: uuid.UUID,
    quantity: Decimal,
    warehouse_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> tuple[InventoryTransaction, InventoryTransaction | None]:
    """
    Produce a batch of an item, consuming its bill of materials.

    Returns (production, consumption). Consumption is None when the item has no
    recipe — some businesses record production of bought-in goods purely to move
    them between units.

    The yield percentage is applied to the *output*: a recipe that loses 10% in
    baking produces 0.9 of what its inputs suggest, so cost per unit rises
    accordingly rather than the loss being invisible.
    """
    output_quantity = _q(quantity)
    if output_quantity <= 0:
        raise BadRequestError("Production quantity must be positive")

    branch_settings = (
        await db.execute(
            select(BranchInventorySettings).where(
                BranchInventorySettings.branch_id == branch.id
            )
        )
    ).scalar_one_or_none()
    if (
        branch_settings is None
        or not branch_settings.inventory_enabled
        or not branch_settings.production_enabled
    ):
        raise ConflictError("Inventory production is not enabled for this branch")
    # One production batch is an atomic value flow: inputs, planned waste and
    # output all use costs observed under this branch's posting lock.
    await source_event_service.lock_branch_inventory(db, branch.id)

    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Inventory item not found")

    business_date = await business_day_service.current_business_date(db, branch)
    warehouse = (
        warehouse_id or (await inventory_service.default_warehouse(db, branch.id)).id
    )

    legacy_recipe = list(
        (
            await db.execute(
                select(InventoryItemIngredient).where(
                    InventoryItemIngredient.parent_item_id == item_id
                )
            )
        )
        .scalars()
        .all()
    )

    consumption: InventoryTransaction | None = None
    input_cost = Decimal("0")

    expanded = None
    used_versions: set[uuid.UUID] = set()
    try:
        expanded, used_versions = await recipe_service.expand_owner(
            db,
            kind="inventory_item",
            owner_id=item_id,
            multiplier=output_quantity,
        )
    except NotFoundError:
        # One compatibility release: a legacy mutable recipe remains usable,
        # while every newly edited recipe goes through versioning.
        expanded = None

    recipe_lines: list[tuple[uuid.UUID, Decimal, Decimal, list, uuid.UUID | None]] = []
    if expanded is not None:
        for ingredient_id, line in expanded.items():
            version_id = (
                next(iter(line.recipe_version_ids))
                if len(line.recipe_version_ids) == 1
                else None
            )
            recipe_lines.append(
                (
                    ingredient_id,
                    line.quantity,
                    line.planned_waste,
                    line.paths,
                    version_id,
                )
            )
    else:
        recipe_lines = [
            (
                line.item_id,
                _q(Decimal(str(line.quantity)) * output_quantity),
                Decimal("0"),
                [],
                None,
            )
            for line in legacy_recipe
        ]

    correction_group = uuid.uuid4()
    if recipe_lines:
        consumption = InventoryTransaction(
            reference=await inventory_service.next_reference(
                db, InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value
            ),
            type=InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value,
            status=TransactionStatusEnum.DRAFT.value,
            branch_id=branch.id,
            warehouse_id=warehouse,
            business_date=business_date,
            creator_id=user.id,
            correction_group_id=correction_group,
            source_type="production",
            source_id=str(item_id),
            notes=f"Ingredients for {output_quantity} x {item.name}",
            items=[],
        )
        db.add(consumption)
        await db.flush()

        planned_waste_lines = []
        for ingredient_id, used, planned_waste, paths, version_id in recipe_lines:
            ingredient = await db.get(InventoryItem, ingredient_id)
            level = await inventory_service.level_for(db, ingredient_id, warehouse)
            cost = _c(
                level.average_cost
                or (
                    inventory_service.inventory_item_cost_for_unit(
                        ingredient, "ingredient"
                    )
                    if ingredient
                    else 0
                )
            )
            input_cost += used * cost
            consumed = _q(used - planned_waste)
            if consumed > 0:
                consumption.items.append(
                    InventoryTransactionItem(
                        item_id=ingredient_id,
                        quantity=consumed,
                        unit="ingredient",
                        conversion_factor=Decimal("1"),
                        unit_cost=cost,
                        recipe_version_id=version_id,
                        recipe_path=paths,
                    )
                )
            if planned_waste > 0:
                planned_waste_lines.append(
                    (ingredient_id, planned_waste, cost, paths, version_id)
                )

        await db.flush()
        consumption = await inventory_service.load_transaction(db, consumption.id)
        consumption = await inventory_service.post_transaction(
            db, transaction=consumption, user=user
        )

        if planned_waste_lines:
            waste = InventoryTransaction(
                reference=await inventory_service.next_reference(
                    db, InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value
                ),
                type=InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value,
                status=TransactionStatusEnum.DRAFT.value,
                branch_id=branch.id,
                warehouse_id=warehouse,
                business_date=business_date,
                creator_id=user.id,
                correction_group_id=correction_group,
                source_type="production_yield",
                source_id=str(item_id),
                notes=f"Planned recipe yield loss for {output_quantity} x {item.name}",
                items=[],
            )
            db.add(waste)
            await db.flush()
            for ingredient_id, wasted, cost, paths, version_id in planned_waste_lines:
                waste.items.append(
                    InventoryTransactionItem(
                        item_id=ingredient_id,
                        quantity=wasted,
                        unit="ingredient",
                        conversion_factor=Decimal("1"),
                        unit_cost=cost,
                        recipe_version_id=version_id,
                        recipe_path=paths,
                    )
                )
            await db.flush()
            waste = await inventory_service.load_transaction(db, waste.id)
            await inventory_service.post_transaction(db, transaction=waste, user=user)

    # Yield loss raises the unit cost of what actually came out of the oven.
    net_output = (
        output_quantity
        if expanded is not None
        else production_output(output_quantity, item.yield_percentage)
    )
    unit_cost = (
        production_unit_cost(input_cost, net_output)
        if recipe_lines
        else inventory_service.inventory_item_cost_for_unit(item, "ingredient")
    )

    production = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.PRODUCTION.value
        ),
        type=InventoryTransactionTypeEnum.PRODUCTION.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=branch.id,
        warehouse_id=warehouse,
        business_date=business_date,
        creator_id=user.id,
        correction_group_id=correction_group,
        source_type="production",
        source_id=str(item_id),
        notes=notes,
        items=[],
    )
    db.add(production)
    await db.flush()
    active_output_version = await recipe_service.active_version(
        db, "inventory_item", item_id
    )
    production.items.append(
        InventoryTransactionItem(
            item_id=item_id,
            quantity=net_output,
            unit="ingredient",
            conversion_factor=Decimal("1"),
            unit_cost=unit_cost,
            recipe_version_id=active_output_version.id
            if active_output_version
            else None,
            # A list of hop-lists, matching the consumption poster's shape
            # (list[list[dict[str, str]]]) so the shared schema is one type, not a
            # widened `list[Any]`. The produced good is one hop per recipe version
            # that fed it.
            recipe_path=[
                [{"recipe_version_id": str(value), "owner_id": str(item_id)}]
                for value in sorted(used_versions, key=str)
            ],
        )
    )
    await db.flush()

    production = await inventory_service.load_transaction(db, production.id)
    production = await inventory_service.post_transaction(
        db, transaction=production, user=user
    )
    return production, consumption
