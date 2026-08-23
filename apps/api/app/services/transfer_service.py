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

# Aliased to the existing private names: the implementation is shared,
# the call sites stay put, and `quantity` is already a local variable in
# both of these files.
from app.core.money import quantity as _q
from app.core.money import unit_cost as _c

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
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
from app.models.operations import (
    TransferOrder,
    TransferOrderStatusEnum,
)
from app.models.user import User
from app.services import business_day_service, inventory_service

__all__ = [
    "production_output",
    "production_unit_cost",
    "accept_transfer_order",
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
    count = int(
        (await db.execute(select(func.count()).select_from(TransferOrder))).scalar_one()
    )
    return f"TO-{count + 1:06d}"


async def load_transfer_order(db: AsyncSession, order_id: uuid.UUID) -> TransferOrder:
    order = (
        (await db.execute(select(TransferOrder).where(TransferOrder.id == order_id)))
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
    if order.status != TransferOrderStatusEnum.PENDING.value:
        raise ConflictError("Only submitted transfer orders can be declined")
    order.status = TransferOrderStatusEnum.DECLINED.value
    order.responder_id = user.id
    order.responded_at = utcnow()
    if notes:
        order.notes = notes
    await db.flush()
    return order


# ─── Movement ─────────────────────────────────────────────────────────────────


async def send_transfer(
    db: AsyncSession, *, order: TransferOrder, user: User
) -> InventoryTransaction:
    """Ship an accepted transfer, decrementing the source location."""
    if order.status != TransferOrderStatusEnum.ACCEPTED.value:
        raise ConflictError("Only accepted transfer orders can be sent")
    if order.sent_transaction_id is not None:
        raise ConflictError("This transfer has already been sent")

    source = await db.get(Branch, order.source_branch_id)
    if source is None:
        raise NotFoundError("Source branch not found")

    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.TRANSFER_SEND.value
        ),
        type=InventoryTransactionTypeEnum.TRANSFER_SEND.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=order.source_branch_id,
        warehouse_id=order.source_warehouse_id,
        other_branch_id=order.branch_id,
        other_warehouse_id=order.warehouse_id,
        business_date=await business_day_service.current_business_date(db, source),
        creator_id=user.id,
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
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=line.item_id,
                quantity=quantity,
                unit=line.unit,
                conversion_factor=line.conversion_factor,
                unit_cost=_q(item.cost) if item else Decimal("0"),
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
) -> InventoryTransaction:
    """
    Book a shipped transfer into the destination.

    A shortfall between sent and received is left as a visible difference rather
    than being quietly reconciled — that gap is exactly what an investigation
    needs to see.
    """
    if order.sent_transaction_id is None:
        raise ConflictError("This transfer has not been sent yet")
    if order.received_transaction_id is not None:
        raise ConflictError("This transfer has already been received")

    destination = await db.get(Branch, order.branch_id)
    if destination is None:
        raise NotFoundError("Destination branch not found")

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
        notes=f"Transfer {order.reference}",
    )
    db.add(transaction)
    await db.flush()

    for line in order.items:
        sent = _q(line.sent_quantity)
        quantity = _q(received.get(line.id, sent)) if received else sent
        if quantity <= 0:
            continue
        if quantity > sent:
            raise BadRequestError(
                f"Cannot receive {quantity} when only {sent} was sent"
            )
        item = await db.get(InventoryItem, line.item_id)
        db.add(
            InventoryTransactionItem(
                transaction_id=transaction.id,
                item_id=line.item_id,
                quantity=quantity,
                unit=line.unit,
                conversion_factor=line.conversion_factor,
                unit_cost=_q(item.cost) if item else Decimal("0"),
            )
        )
        line.received_quantity = quantity

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

    item = await db.get(InventoryItem, item_id)
    if item is None:
        raise NotFoundError("Inventory item not found")

    business_date = await business_day_service.current_business_date(db, branch)
    warehouse = (
        warehouse_id or (await inventory_service.default_warehouse(db, branch.id)).id
    )

    recipe = list(
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

    if recipe:
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
            notes=f"Ingredients for {output_quantity} x {item.name}",
        )
        db.add(consumption)
        await db.flush()

        for line in recipe:
            ingredient = await db.get(InventoryItem, line.item_id)
            used = _q(Decimal(str(line.quantity)) * output_quantity)
            unit_cost = Decimal(str(ingredient.cost)) if ingredient else Decimal("0")
            input_cost += used * unit_cost
            db.add(
                InventoryTransactionItem(
                    transaction_id=consumption.id,
                    item_id=line.item_id,
                    quantity=used,
                    unit="ingredient",
                    conversion_factor=Decimal("1"),
                    unit_cost=unit_cost,
                )
            )

        await db.flush()
        consumption = await inventory_service.load_transaction(db, consumption.id)
        consumption = await inventory_service.post_transaction(
            db, transaction=consumption, user=user
        )

    # Yield loss raises the unit cost of what actually came out of the oven.
    net_output = production_output(output_quantity, item.yield_percentage)
    unit_cost = (
        production_unit_cost(input_cost, net_output)
        if recipe
        else Decimal(str(item.cost))
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
        notes=notes,
    )
    db.add(production)
    await db.flush()
    db.add(
        InventoryTransactionItem(
            transaction_id=production.id,
            item_id=item_id,
            quantity=net_output,
            unit="ingredient",
            conversion_factor=Decimal("1"),
            unit_cost=unit_cost,
        )
    )
    await db.flush()

    production = await inventory_service.load_transaction(db, production.id)
    production = await inventory_service.post_transaction(
        db, transaction=production, user=user
    )
    return production, consumption
