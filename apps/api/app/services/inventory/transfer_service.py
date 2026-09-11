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
from app.models.base import utcnow
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
    TransferKindEnum,
    TransferOrder,
    TransferOrderItem,
    TransferOrderStatusEnum,
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
    "accept_transfer_order",
    "create_and_send",
    "decline_transfer_order",
    "produce",
    "receive_transfer",
    "send_transfer",
    "submit_transfer_order",
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


async def next_transfer_reference(db: AsyncSession) -> str:
    return await inventory_service.next_inventory_reference(db, "TO")


async def load_transfer_order(db: AsyncSession, order_id: uuid.UUID) -> TransferOrder:
    order = (
        (
            await db.execute(
                select(TransferOrder)
                .where(TransferOrder.id == order_id)
                .options(selectinload(TransferOrder.items))
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if order is None:
        raise NotFoundError("Transfer order not found")
    return order


async def _lock_transfer_order(db: AsyncSession, order_id: uuid.UUID) -> TransferOrder:
    """Serialize every state transition and stock movement for one transfer."""
    order = (
        (
            await db.execute(
                select(TransferOrder)
                .where(TransferOrder.id == order_id)
                .options(selectinload(TransferOrder.items))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .unique()
        .one_or_none()
    )
    if order is None:
        raise NotFoundError("Transfer order not found")
    return order


# ─── Request workflow ─────────────────────────────────────────────────────────
#
# Left in this shape deliberately, having been compared against the other two
# state machines when the purchase-order one was pulled out of its router.
#
# `order_lifecycle` and `inventory_service`'s purchase-order machine are both a
# declarative map: a transfer's is not, because a transfer's state is not in its
# status column alone. `send_transfer` moves stock and does **not** change
# `status` — an accepted order stays accepted and grows a `sent_transaction_id`;
# `receive_transfer` gates on that id, not on the status, and only then closes
# the order. The composite is real rather than an oversight: it is what lets a
# transfer be simultaneously "accepted" and "on a van", which is the state the
# whole design exists to make visible.
#
# A `status -> status` map cannot express "may send if accepted and not already
# sent", so forcing one here would either lose the two link columns or turn them
# into two more statuses that mean less than the columns do. The guards below
# stay imperative, but they stay in a *service* — which was the actual complaint:
# three homes for the pattern, one of them a router.


async def submit_transfer_order(
    db: AsyncSession, *, order: TransferOrder, user: User
) -> TransferOrder:
    order = await _lock_transfer_order(db, order.id)
    if order.status != TransferOrderStatusEnum.DRAFT.value:
        raise ConflictError(
            f"Only draft transfer orders can be submitted (this is {order.status})"
        )
    if not order.items:
        raise BadRequestError("A transfer order needs at least one line")

    order.status = TransferOrderStatusEnum.PENDING.value
    order.submitter_id = user.id
    order.submitted_at = utcnow()
    await db.flush()
    return order


async def accept_transfer_order(
    db: AsyncSession,
    *,
    order: TransferOrder,
    user: User,
    approved: dict[uuid.UUID, Decimal] | None = None,
) -> TransferOrder:
    """
    Accept a request, optionally trimming quantities.

    A source branch that only has half of what was asked for should be able to
    say so up front rather than silently short-shipping later.
    """
    order = await _lock_transfer_order(db, order.id)
    if order.status != TransferOrderStatusEnum.PENDING.value:
        raise ConflictError("Only submitted transfer orders can be accepted")

    for line in order.items:
        requested = _q(line.quantity)
        granted = _q(approved.get(line.id, requested)) if approved else requested
        if granted < 0 or granted > requested:
            raise BadRequestError(
                f"Approved quantity must be between 0 and the {requested} requested"
            )
        line.approved_quantity = granted

    if all(_q(line.approved_quantity) == 0 for line in order.items):
        raise BadRequestError("Accepting with every line at zero — decline instead")

    order.status = TransferOrderStatusEnum.ACCEPTED.value
    order.responder_id = user.id
    order.responded_at = utcnow()
    await db.flush()
    return order


async def decline_transfer_order(
    db: AsyncSession, *, order: TransferOrder, user: User, notes: str | None = None
) -> TransferOrder:
    order = await _lock_transfer_order(db, order.id)
    if order.status != TransferOrderStatusEnum.PENDING.value:
        raise ConflictError("Only submitted transfer orders can be declined")
    order.status = TransferOrderStatusEnum.DECLINED.value
    order.responder_id = user.id
    order.responded_at = utcnow()
    if notes:
        order.notes = notes
    await db.flush()
    return order


# ─── Source-initiated push ────────────────────────────────────────────────────


async def create_and_send(
    db: AsyncSession,
    *,
    source_branch: Branch,
    destination_branch: Branch,
    user: User,
    lines: list,
    kind: str = TransferKindEnum.TRANSFER.value,
    notes: str | None = None,
    client_request_id: str | None = None,
    template_id: uuid.UUID | None = None,
) -> TransferOrder:
    """Raise a transfer at the *sending* branch and ship it in one step.

    The till model is a push, not the request/accept pull the admin console uses:
    a shop packs a box for another branch and sends it. So the order is created
    already accepted (every line granted in full) and ``send_transfer`` posts the
    ``TRANSFER_SEND`` immediately — the source's stock leaves the moment the box
    does. The receiving branch then books what actually arrived.

    ``lines`` is any object list carrying ``item_id``, ``quantity``, ``unit`` and
    an optional ``variance_reason`` (the reason a return line is going back).

    When ``template_id`` is given, that exact template version is snapshotted onto
    the order (``template_id``/``template_version``/``template_snapshot``) so the
    order's provenance survives a later edit or deactivation of the live template —
    downstream reads the snapshot, never the live row. Absent for a return or an
    ad-hoc transfer, and the three columns stay null.
    """
    if source_branch.id == destination_branch.id:
        raise BadRequestError("A branch cannot transfer to itself")
    if not lines:
        raise BadRequestError("A transfer needs at least one line")

    # A retried create+send (lost response) reuses its token; return the order
    # already shipped rather than shipping the box a second time.
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

    order = TransferOrder(
        reference=await next_transfer_reference(db),
        kind=kind,
        client_request_id=client_request_id,
        status=TransferOrderStatusEnum.ACCEPTED.value,
        branch_id=destination_branch.id,
        source_branch_id=source_branch.id,
        business_date=await business_day_service.current_business_date(
            db, source_branch
        ),
        notes=notes,
        creator_id=user.id,
        submitter_id=user.id,
        responder_id=user.id,
        submitted_at=utcnow(),
        responded_at=utcnow(),
        template_id=template_id,
        template_version=template_version,
        template_snapshot=template_snapshot,
    )
    # The pre-check above catches a sequential retry; this catches two truly
    # concurrent submits — the unique index refuses the loser, and we return the
    # order the winner already made rather than 500ing.
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

    for line in lines:
        item = await db.get(InventoryItem, line.item_id)
        if item is None:
            raise BadRequestError(f"Inventory item {line.item_id} not found")
        unit = getattr(line, "unit", "storage")
        db.add(
            TransferOrderItem(
                transfer_order_id=order.id,
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

    order = await load_transfer_order(db, order.id)
    await send_transfer(db, order=order, user=user)

    # A branch that does not run the POS (DSO, Karama) has no till to receive on:
    # book the whole shipment straight into it so its on-hand is correct and the
    # transfer is not left forever "to receive" at a branch that cannot act on it.
    if not getattr(destination_branch, "uses_pos", True):
        order = await load_transfer_order(db, order.id)
        await receive_transfer(db, order=order, user=user)

    return await load_transfer_order(db, order.id)


# ─── Movement ─────────────────────────────────────────────────────────────────


async def send_transfer(
    db: AsyncSession, *, order: TransferOrder, user: User
) -> InventoryTransaction:
    """Ship an accepted transfer, decrementing the source location."""
    order = await _lock_transfer_order(db, order.id)
    if order.sent_transaction_id is not None:
        return await inventory_service.load_transaction(db, order.sent_transaction_id)
    if order.status != TransferOrderStatusEnum.ACCEPTED.value:
        raise ConflictError("Only accepted transfer orders can be sent")

    source = await db.get(Branch, order.source_branch_id)
    if source is None:
        raise NotFoundError("Source branch not found")
    # The source moving average is part of the immutable transfer snapshot, so
    # it must be read under the same branch lock that will issue the stock.
    await source_event_service.lock_branch_inventory(db, order.source_branch_id)
    source_warehouse = (
        await inventory_service.assert_warehouse_for_branch(
            db, order.source_warehouse_id, order.source_branch_id
        )
        if order.source_warehouse_id is not None
        else await inventory_service.default_warehouse(db, order.source_branch_id)
    )

    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.TRANSFER_SEND.value
        ),
        type=InventoryTransactionTypeEnum.TRANSFER_SEND.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=order.source_branch_id,
        warehouse_id=source_warehouse.id,
        other_branch_id=order.branch_id,
        other_warehouse_id=order.warehouse_id,
        business_date=await business_day_service.current_business_date(db, source),
        creator_id=user.id,
        idempotency_key=f"transfer:{order.id}:send",
        source_type="transfer_order",
        source_id=str(order.id),
        notes=f"Transfer {order.reference}",
    )
    db.add(transaction)
    await db.flush()

    for line in order.items:
        quantity = _q(
            line.approved_quantity
            if line.approved_quantity is not None
            else line.quantity
        )
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
        line.sent_quantity = quantity

    await db.flush()
    transaction = await inventory_service.load_transaction(db, transaction.id)
    posted = await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )
    order.sent_transaction_id = posted.id
    await db.flush()
    return posted


async def receive_transfer(
    db: AsyncSession,
    *,
    order: TransferOrder,
    user: User,
    received: dict[uuid.UUID, Decimal] | None = None,
    reasons: dict[uuid.UUID, str] | None = None,
) -> InventoryTransaction:
    """
    Book a shipped transfer into the destination — what actually arrived, which
    may be less **or more** than was sent.

    The destination rises by ``received`` and the source has already fallen by
    ``sent``, so the sent-vs-received difference is never silently absorbed: a
    short receipt leaves the shortfall on the *sender's* books (the goods left
    their shelf and did not arrive — a transit loss they own), and an over-receipt
    is a genuine gain at the destination. Both are recorded on the line as a
    ``received_quantity`` that differs from ``sent_quantity``, with the receiver's
    ``reason`` (``reasons`` keyed by line id), so the transfer record itself
    explains every unit. Posting a *separate* shrinkage/found movement here would
    double-count, because the send already moved the stock.
    """
    order = await _lock_transfer_order(db, order.id)
    if order.received_transaction_id is not None:
        return await inventory_service.load_transaction(
            db, order.received_transaction_id
        )
    if order.sent_transaction_id is None:
        raise ConflictError("This transfer has not been sent yet")

    destination = await db.get(Branch, order.branch_id)
    if destination is None:
        raise NotFoundError("Destination branch not found")
    sent_transaction = await inventory_service.load_transaction(
        db, order.sent_transaction_id
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
        branch_id=order.branch_id,
        warehouse_id=order.warehouse_id,
        other_branch_id=order.source_branch_id,
        other_warehouse_id=order.source_warehouse_id,
        business_date=await business_day_service.current_business_date(db, destination),
        creator_id=user.id,
        idempotency_key=f"transfer:{order.id}:receive",
        source_type="transfer_order",
        source_id=str(order.id),
        notes=f"Transfer {order.reference}",
    )
    db.add(transaction)
    await db.flush()

    for line in order.items:
        sent = _q(line.sent_quantity)
        quantity = _q(received.get(line.id, sent)) if received else sent
        # The receiver's note explains a discrepancy — short (lost in transit) or
        # over (found). Recorded whether or not anything is booked below.
        if reasons is not None and line.id in reasons:
            line.variance_reason = reasons[line.id] or None
        line.received_quantity = quantity if quantity > 0 else _q(0)
        if quantity <= 0:
            continue
        # An over-receipt is valued at the sent unit cost — the same goods, more of
        # them than the paperwork said.
        sent_cost = sent_costs.get(f"transfer_item:{line.id}")
        if sent_cost is None:
            if _q(line.sent_quantity) <= 0:
                # Nothing shipped on this line, so there is no cost to value a
                # receipt against — a "found extra" here belongs on a stock count.
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
    order.received_transaction_id = posted.id
    order.status = TransferOrderStatusEnum.CLOSED.value
    await db.flush()
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
