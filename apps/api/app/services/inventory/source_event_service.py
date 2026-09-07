"""Freeze and post inventory effects in MM acceptance order.

The branch advisory lock is acquired before an acceptance sequence is allocated,
so two concurrent finalized orders cannot update the cached balance in provider
timestamp order or commit order by accident.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import advisory_lock, heartbeat
from app.core.database import SchedulerSessionFactory
from app.core.exceptions import AppError, BadRequestError, ConflictError
from app.core.money import unit_cost
from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventorySourceEvent,
    InventorySourceEventStatusEnum,
)
from app.models.order import Order
from app.models.user import User
from app.services.inventory import inventory_service, recipe_service
from app.services.pos import business_day_service

logger = logging.getLogger(__name__)


async def lock_branch_inventory(db: AsyncSession, branch_id: uuid.UUID) -> None:
    """Transaction-scoped serialization for every stock event in one branch."""
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"inventory:{branch_id}", 0)
            )
        )
    )


async def accept_order(
    db: AsyncSession, *, order: Order, user: User | None
) -> InventorySourceEvent | None:
    if order.branch_id is None:
        return None
    settings = (
        (
            await db.execute(
                select(BranchInventorySettings).where(
                    BranchInventorySettings.branch_id == order.branch_id
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if (
        settings is None
        or not settings.inventory_enabled
        or not settings.sales_consumption_enabled
    ):
        return None

    key = f"order:{order.id}:1"
    existing = (
        (
            await db.execute(
                select(InventorySourceEvent).where(
                    InventorySourceEvent.idempotency_key == key
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing

    await lock_branch_inventory(db, order.branch_id)
    # Re-check after waiting for the lock: another request may have won while
    # this transaction was blocked.
    existing = (
        (
            await db.execute(
                select(InventorySourceEvent).where(
                    InventorySourceEvent.idempotency_key == key
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing is not None:
        return existing

    plan, warnings = await recipe_service.snapshot_order(db, order)
    if warnings:
        plan["warnings"] = warnings
    # A missing recipe on ONE product or modifier must no longer suppress the rest
    # of the order (the old dead-end EXCEPTION posted nothing). Two cases:
    #   * NOTHING expands (every line is missing a recipe): the event stays PENDING
    #     and carries ``missing_recipe``, so the sweeper and the retry endpoint
    #     re-snapshot it once the recipe is activated. This is the common single-
    #     product case behind the current prod backlog.
    #   * SOME lines expand: those are posted now and the event finalises POSTED
    #     (one movement per event, guarded by the transaction idempotency key —
    #     it cannot be re-posted). ``missing_recipe`` is kept on the row as a
    #     breadcrumb, but the still-missing lines are not retried; activate the
    #     recipe before such an order is placed. (Fuller partial-retry would need
    #     more than one movement per event — deliberately out of scope here.)
    event = InventorySourceEvent(
        branch_id=order.branch_id,
        source_type="order",
        source_id=str(order.id),
        source_revision=1,
        idempotency_key=key,
        status=InventorySourceEventStatusEnum.PENDING.value,
        occurred_at=order.created_at,
        accepted_at=utcnow(),
        frozen_plan=plan,
        recipe_version_ids=plan.get("recipe_version_ids", []),
        error_code="missing_recipe" if warnings else None,
        error_detail="; ".join(warnings) if warnings else None,
        processed_at=None,
    )
    db.add(event)
    await db.flush()
    # Post whatever expanded. When nothing expanded but a recipe is missing, leave
    # it PENDING for re-snapshot rather than finalising an empty movement.
    if plan.get("lines") or not warnings:
        await _post_or_record_exception(
            db, event=event, order=order, user=user, already_locked=True
        )
    return event


async def _post_or_record_exception(
    db: AsyncSession,
    *,
    event: InventorySourceEvent,
    order: Order,
    user: User | None,
    already_locked: bool,
) -> InventoryTransaction | None:
    """Post atomically, preserving a sequenced no-movement domain failure."""
    try:
        async with db.begin_nested():
            return await post_event(
                db,
                event=event,
                order=order,
                user=user,
                already_locked=already_locked,
            )
    except AppError as exc:
        # The savepoint undoes every level/transaction mutation. Refresh the
        # pre-existing outbox row before turning it into the immutable gap at
        # this acceptance sequence; later orders must never jump in front of it.
        await db.refresh(event)
        event.status = InventorySourceEventStatusEnum.EXCEPTION.value
        event.error_code = "inventory_posting_failed"
        event.error_detail = exc.detail
        event.processed_at = utcnow()
        await db.flush()
        return None


async def post_event(
    db: AsyncSession,
    *,
    event: InventorySourceEvent,
    order: Order,
    user: User | None,
    already_locked: bool = False,
) -> InventoryTransaction | None:
    if event.status == InventorySourceEventStatusEnum.POSTED.value:
        return await db.get(InventoryTransaction, event.transaction_id)
    if event.status not in {
        InventorySourceEventStatusEnum.PENDING.value,
        InventorySourceEventStatusEnum.PROCESSING.value,
    }:
        return None
    if not already_locked:
        await lock_branch_inventory(db, event.branch_id)

    lines = event.frozen_plan.get("lines", [])
    if not lines:
        if event.error_code == "missing_recipe":
            # Nothing expanded because a recipe is missing. Do NOT finalise an
            # empty movement — that would strand the order at zero consumption
            # forever. Leave it PENDING so the sweeper/retry re-snapshots it once
            # the recipe exists.
            return None
        event.status = InventorySourceEventStatusEnum.POSTED.value
        event.processed_at = utcnow()
        await db.flush()
        return None

    branch = await db.get(Branch, event.branch_id)
    if branch is None:
        event.status = InventorySourceEventStatusEnum.EXCEPTION.value
        event.error_code = "branch_not_found"
        event.error_detail = f"Branch {event.branch_id} no longer exists"
        event.processed_at = utcnow()
        await db.flush()
        return None

    transaction = InventoryTransaction(
        reference=await inventory_service.next_reference(
            db, InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value
        ),
        type=InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
        status=TransactionStatusEnum.DRAFT.value,
        branch_id=event.branch_id,
        business_date=order.business_date
        or await business_day_service.current_business_date(db, branch),
        order_id=order.id,
        creator_id=user.id if user else None,
        occurred_at=event.occurred_at,
        source_accepted_sequence=event.accepted_sequence,
        idempotency_key=f"inventory-event:{event.id}",
        source_type=event.source_type,
        source_id=event.source_id,
        notes=f"Frozen recipe consumption for {order.order_number}",
        items=[],
    )
    db.add(transaction)
    await db.flush()
    warehouse = await inventory_service.default_warehouse(db, event.branch_id)
    transaction.warehouse_id = warehouse.id

    for frozen in lines:
        item_id = uuid.UUID(frozen["item_id"])
        item = await db.get(InventoryItem, item_id)
        if item is None:
            event.status = InventorySourceEventStatusEnum.EXCEPTION.value
            event.error_code = "inventory_item_not_found"
            event.error_detail = f"Inventory item {item_id} no longer exists"
            event.processed_at = utcnow()
            await db.delete(transaction)
            await db.flush()
            return None
        level = await inventory_service.level_for(db, item_id, warehouse.id)
        version_ids = frozen.get("recipe_version_ids", [])
        transaction.items.append(
            InventoryTransactionItem(
                item_id=item_id,
                quantity=Decimal(frozen["quantity"]),
                unit="ingredient",
                conversion_factor=Decimal("1"),
                unit_cost=unit_cost(
                    level.average_cost
                    or inventory_service.inventory_item_cost_for_unit(
                        item, "ingredient"
                    )
                ),
                recipe_version_id=(
                    uuid.UUID(version_ids[0]) if len(version_ids) == 1 else None
                ),
                recipe_path=frozen.get("paths", []),
            )
        )

    event.status = InventorySourceEventStatusEnum.PROCESSING.value
    await db.flush()
    posted = await inventory_service.post_transaction(
        db, transaction=transaction, user=user
    )
    event.status = InventorySourceEventStatusEnum.POSTED.value
    event.transaction_id = posted.id
    event.processed_at = utcnow()
    await db.flush()
    return posted


async def retry_event(
    db: AsyncSession,
    *,
    event: InventorySourceEvent,
    order: Order,
    user: User | None,
    already_locked: bool = False,
) -> InventoryTransaction | None:
    """Re-snapshot a never-posted event so a recipe activated since acceptance lands.

    A ``missing_recipe`` event freezes only the lines that expanded at acceptance;
    the frozen plan never grows on its own. This re-runs ``snapshot_order`` against
    the live recipe graph, replaces the (never-applied) frozen plan, and posts.
    Guarded to events that have not posted a movement, so it can never double-count
    what a partial acceptance already consumed.
    """
    if (
        event.status == InventorySourceEventStatusEnum.POSTED.value
        or event.transaction_id is not None
    ):
        raise ConflictError("This inventory event has already posted a movement")
    if event.source_type != "order":
        raise BadRequestError("Only an order event can be re-snapshotted")
    if not already_locked:
        await lock_branch_inventory(db, event.branch_id)

    plan, warnings = await recipe_service.snapshot_order(db, order)
    if warnings:
        plan["warnings"] = warnings
    # The plan was never applied (guarded above), so re-freezing it is safe.
    event.frozen_plan = plan
    event.recipe_version_ids = plan.get("recipe_version_ids", [])
    event.error_code = "missing_recipe" if warnings else None
    event.error_detail = "; ".join(warnings) if warnings else None
    event.status = InventorySourceEventStatusEnum.PENDING.value
    event.processed_at = None
    await db.flush()

    if not plan.get("lines"):
        # A recipe is still missing everywhere; keep it recoverable.
        return None
    return await _post_or_record_exception(
        db, event=event, order=order, user=user, already_locked=True
    )


async def record_order_cancellation(db: AsyncSession, order: Order) -> None:
    """Cancel an unposted source event or require a posted-return disposition."""
    # Legacy/provider test doubles and orders not yet routed to a branch have no
    # inventory scope. There cannot be an accepted inventory event to cancel.
    if getattr(order, "branch_id", None) is None:
        return
    await lock_branch_inventory(db, order.branch_id)
    accepted = (
        (
            await db.execute(
                select(InventorySourceEvent)
                .where(
                    InventorySourceEvent.source_type == "order",
                    InventorySourceEvent.source_id == str(order.id),
                )
                .order_by(InventorySourceEvent.accepted_sequence)
            )
        )
        .scalars()
        .first()
    )
    if accepted is None:
        return
    if accepted.status in {
        InventorySourceEventStatusEnum.PENDING.value,
        InventorySourceEventStatusEnum.PROCESSING.value,
    }:
        accepted.status = InventorySourceEventStatusEnum.CANCELLED.value
        accepted.processed_at = utcnow()
        await db.flush()
        return
    if (
        accepted.status != InventorySourceEventStatusEnum.POSTED.value
        or accepted.transaction_id is None
    ):
        return
    key = f"order-cancel:{order.id}:1"
    exists = await db.scalar(
        select(InventorySourceEvent.id).where(
            InventorySourceEvent.idempotency_key == key
        )
    )
    if exists is not None:
        return
    db.add(
        InventorySourceEvent(
            branch_id=order.branch_id,
            source_type="order_return",
            source_id=str(order.id),
            source_revision=1,
            idempotency_key=key,
            status=InventorySourceEventStatusEnum.EXCEPTION.value,
            occurred_at=utcnow(),
            accepted_at=utcnow(),
            frozen_plan={
                "reason": "order_cancelled_after_inventory_posting",
                "original_transaction_id": str(accepted.transaction_id),
            },
            recipe_version_ids=accepted.recipe_version_ids,
            error_code="return_disposition_required",
            error_detail="Choose restock, waste, or no inventory effect for this cancellation",
            processed_at=utcnow(),
        )
    )
    await db.flush()


async def _resolve_cancellation_exception(
    db: AsyncSession,
    *,
    order_id: uuid.UUID,
    transaction_id: uuid.UUID | None,
) -> None:
    event = (
        (
            await db.execute(
                select(InventorySourceEvent).where(
                    InventorySourceEvent.idempotency_key == f"order-cancel:{order_id}:1"
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if event is None:
        return
    event.status = InventorySourceEventStatusEnum.POSTED.value
    event.transaction_id = transaction_id
    event.error_code = None
    event.error_detail = None
    event.processed_at = utcnow()
    await db.flush()


async def record_return(
    db: AsyncSession,
    *,
    order: Order,
    user: User,
    disposition: str,
    proportion: Decimal,
    idempotency_key: str,
    notes: str | None = None,
) -> list[InventoryTransaction]:
    """Apply a partial historical return from the original frozen movement."""
    if disposition not in {"restock", "waste", "no_inventory_effect"}:
        raise BadRequestError("Unknown inventory return disposition")
    if proportion <= 0 or proportion > 1:
        raise BadRequestError(
            "Return proportion must be greater than zero and at most one"
        )
    if order.branch_id is None:
        return []
    await lock_branch_inventory(db, order.branch_id)
    disposition_event_key = f"order-return:{idempotency_key}"
    existing_event = (
        (
            await db.execute(
                select(InventorySourceEvent).where(
                    InventorySourceEvent.idempotency_key == disposition_event_key
                )
            )
        )
        .scalars()
        .one_or_none()
    )
    if existing_event is not None and disposition == "no_inventory_effect":
        await _resolve_cancellation_exception(
            db, order_id=order.id, transaction_id=None
        )
        return []
    prior = list(
        (
            await db.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.idempotency_key.in_(
                        [f"{idempotency_key}:return", f"{idempotency_key}:waste"]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    if prior:
        await _resolve_cancellation_exception(
            db, order_id=order.id, transaction_id=prior[-1].id
        )
        return prior
    original = (
        (
            await db.execute(
                select(InventoryTransaction).where(
                    InventoryTransaction.order_id == order.id,
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.CONSUMPTION_FROM_ORDERS.value,
                    InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                )
            )
        )
        .scalars()
        .first()
    )
    if original is None:
        return []
    if disposition == "no_inventory_effect":
        db.add(
            InventorySourceEvent(
                branch_id=order.branch_id,
                source_type="order_return",
                source_id=str(order.id),
                source_revision=1,
                idempotency_key=disposition_event_key,
                status=InventorySourceEventStatusEnum.POSTED.value,
                occurred_at=utcnow(),
                accepted_at=utcnow(),
                frozen_plan={
                    "disposition": disposition,
                    "proportion": str(proportion),
                    "notes": notes,
                    "original_transaction_id": str(original.id),
                },
                recipe_version_ids=[],
                processed_at=utcnow(),
            )
        )
        await db.flush()
        await _resolve_cancellation_exception(
            db, order_id=order.id, transaction_id=None
        )
        return []
    previous_returns = list(
        (
            await db.execute(
                select(InventoryTransaction)
                .where(
                    InventoryTransaction.order_id == order.id,
                    InventoryTransaction.type
                    == InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value,
                    InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                )
                .options(selectinload(InventoryTransaction.items))
            )
        )
        .scalars()
        .unique()
        .all()
    )
    returned_by_item: dict[uuid.UUID, Decimal] = {}
    for transaction in previous_returns:
        for line in transaction.items:
            returned_by_item[line.item_id] = returned_by_item.get(
                line.item_id, Decimal("0")
            ) + abs(Decimal(str(line.signed_quantity)))
    for line in original.items:
        original_quantity = abs(Decimal(str(line.signed_quantity)))
        requested = original_quantity * proportion
        if (
            returned_by_item.get(line.item_id, Decimal("0")) + requested
            > original_quantity
        ):
            raise ConflictError(
                "The requested quantity exceeds the unreturned inventory"
            )
    original = await inventory_service.load_transaction(db, original.id)
    branch = await db.get(Branch, order.branch_id)
    business_date = (
        order.business_date
        or await business_day_service.current_business_date(db, branch)
    )
    group = uuid.uuid4()
    waste_reclassification_costs: dict[uuid.UUID, Decimal] = {}
    if disposition == "waste":
        warehouse = await inventory_service.default_warehouse(db, order.branch_id)
        for line in original.items:
            level = await inventory_service.level_for(db, line.item_id, warehouse.id)
            waste_reclassification_costs[line.item_id] = unit_cost(
                level.average_cost
                or inventory_service.line_cost_in_ingredient_unit(line)
            )

    async def movement(kind: str, suffix: str) -> InventoryTransaction:
        transaction = InventoryTransaction(
            reference=await inventory_service.next_reference(db, kind),
            type=kind,
            status=TransactionStatusEnum.DRAFT.value,
            branch_id=order.branch_id,
            warehouse_id=original.warehouse_id,
            business_date=business_date,
            order_id=order.id,
            creator_id=user.id,
            source_type="order_return",
            source_id=str(order.id),
            idempotency_key=f"{idempotency_key}:{suffix}",
            correction_group_id=group,
            notes=notes,
            items=[],
        )
        db.add(transaction)
        await db.flush()
        for line in original.items:
            transaction.items.append(
                InventoryTransactionItem(
                    item_id=line.item_id,
                    quantity=abs(Decimal(str(line.signed_quantity))) * proportion,
                    unit="ingredient",
                    conversion_factor=Decimal("1"),
                    # Waste disposition is a reclassification: the return and
                    # immediate waste must leave both quantity *and valuation*
                    # unchanged. A genuine restock retains the original sale
                    # cost snapshot as required for historical returns.
                    unit_cost=waste_reclassification_costs.get(
                        line.item_id,
                        inventory_service.line_cost_in_ingredient_unit(line),
                    ),
                    recipe_version_id=line.recipe_version_id,
                    recipe_path=line.recipe_path or [],
                )
            )
        await db.flush()
        return await inventory_service.post_transaction(
            db, transaction=transaction, user=user
        )

    returned = await movement(
        InventoryTransactionTypeEnum.RETURN_FROM_ORDERS.value, "return"
    )
    if disposition == "restock":
        await _resolve_cancellation_exception(
            db, order_id=order.id, transaction_id=returned.id
        )
        return [returned]
    wasted = await movement(
        InventoryTransactionTypeEnum.WASTE_FROM_ORDERS.value, "waste"
    )
    await _resolve_cancellation_exception(
        db, order_id=order.id, transaction_id=wasted.id
    )
    return [returned, wasted]


#: Leader election for the sweeper (WP5, F-OPS-2). "mmBINV\0\1" — its OWN key, so
#: only one slot ever sweeps, distinct from the per-branch inventory xact lock the
#: acceptance path takes. Before this the sweeper had no leader election at all:
#: every slot swept, each taking a BLOCKING `pg_advisory_xact_lock` per branch on
#: a held pool connection, with no `lock_timeout` — a live acceptance could stall
#: the whole sweep, on the request pool, every five seconds.
_SWEEPER_LEADER_LOCK_KEY = 0x6D6D_4249_4E56_0001

#: The sweeper wakes this often. 30s, not the old 5s: this is recovery of orders
#: whose recipe was missing at acceptance, nothing a customer waits on, and 5s
#: spent a connection and a per-branch lock twelve times a minute for nothing.
_SWEEPER_TICK_SECONDS = 30

#: Hard budget on one sweep so a wedged branch cannot pin the leader connection.
_SWEEPER_BUDGET_SECONDS = 30


async def _try_lock_branch_inventory(db: AsyncSession, branch_id: uuid.UUID) -> bool:
    """Non-blocking `lock_branch_inventory` for the sweeper.

    The acceptance path takes the branch lock with `pg_advisory_xact_lock`
    (blocking) so two finalizing orders serialise. The sweeper must not block on
    it — a live acceptance holding the lock would otherwise stall the whole pass —
    so it takes the SAME key with `pg_try_advisory_xact_lock`: got it, sweep the
    branch; did not, another transaction (an acceptance, or a stray pass) is on
    it, so skip and let the next tick recover it. `lock_timeout` is set too, so
    no other lock the sweep touches can hang past the budget either.
    """
    await db.execute(text("SET LOCAL lock_timeout = '3s'"))
    got = await db.scalar(
        select(
            func.pg_try_advisory_xact_lock(
                func.hashtextextended(f"inventory:{branch_id}", 0)
            )
        )
    )
    return bool(got)


async def _pending_branch_ids() -> list[uuid.UUID]:
    """Branches with pending events, read on a session of their own and released
    before any per-branch work opens one."""
    async with SchedulerSessionFactory() as db:
        return list(
            (
                await db.execute(
                    select(InventorySourceEvent.branch_id)
                    .where(
                        InventorySourceEvent.status
                        == InventorySourceEventStatusEnum.PENDING.value
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )


async def _sweep_branch_pending(db: AsyncSession, branch_id: uuid.UUID) -> int:
    """Process one branch's pending events in acceptance order.

    The caller holds the branch try-lock in this transaction and commits it.
    """
    events = list(
        (
            await db.execute(
                select(InventorySourceEvent)
                .where(
                    InventorySourceEvent.branch_id == branch_id,
                    InventorySourceEvent.status
                    == InventorySourceEventStatusEnum.PENDING.value,
                )
                .order_by(InventorySourceEvent.accepted_sequence)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    processed = 0
    for event in events:
        if event.source_type != "order":
            continue
        order = await db.get(Order, uuid.UUID(event.source_id))
        if order is None:
            event.status = InventorySourceEventStatusEnum.EXCEPTION.value
            event.error_code = "order_not_found"
            event.error_detail = "Order no longer exists"
            event.processed_at = utcnow()
            continue
        if event.error_code == "missing_recipe":
            # Re-snapshot: a recipe may have been activated since this order was
            # accepted, and the frozen plan does not grow on its own.
            await retry_event(
                db, event=event, order=order, user=None, already_locked=True
            )
        else:
            await _post_or_record_exception(
                db, event=event, order=order, user=None, already_locked=True
            )
        processed += 1
    return processed


async def sweep_pending_once() -> int:
    """Recover pending accepted events in strict per-branch acceptance order.

    ONE session PER BRANCH (WP5, F-OPS-2): each branch is try-locked, its pending
    events processed and committed, and the connection handed back before the
    next branch opens one — so a slow branch can neither hold a scheduler
    connection across every other branch's work nor pin the whole sweep behind
    one live acceptance.
    """
    branch_ids = await _pending_branch_ids()
    processed = 0
    for branch_id in branch_ids:
        async with SchedulerSessionFactory() as db:
            try:
                if not await _try_lock_branch_inventory(db, branch_id):
                    await db.rollback()
                    continue
                processed += await _sweep_branch_pending(db, branch_id)
                # This worker owns the session and has no request dependency to
                # commit it; one commit per branch makes that branch durable.
                await db.commit()
            except Exception:  # noqa: BLE001 — one branch must not stop the rest
                await db.rollback()
                logger.exception(
                    "Inventory source-event sweep failed for branch %s", branch_id
                )
    return processed


async def run_sweeper_forever() -> None:
    """The loop. Leader-elected, 30s cadence sleeping first, a per-tick budget.

    Cancelled on shutdown; never allowed to die on an exception or a timeout —
    one wedged sweep must not stop every future one.
    """
    logger.info(
        "Inventory source-event sweeper started (every %ss)", _SWEEPER_TICK_SECONDS
    )
    while True:
        try:
            # Sleeps FIRST: boot is the busiest moment a process has, and this is
            # recovery work, not anything a customer is waiting on.
            await asyncio.sleep(_SWEEPER_TICK_SECONDS)
            await heartbeat.beat("inventory_source_event_sweeper")
            async with advisory_lock.held(
                _SWEEPER_LEADER_LOCK_KEY, name="inventory source-event sweeper"
            ) as mine:
                if not mine:
                    continue
                async with asyncio.timeout(_SWEEPER_BUDGET_SECONDS):
                    await sweep_pending_once()
        except asyncio.CancelledError:
            raise
        except (Exception, TimeoutError):  # noqa: BLE001 — the next sweep must still run
            logger.exception("Inventory source-event sweep failed")
