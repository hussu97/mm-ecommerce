"""
The database half of FIFO costing v3: load the ledger, run the engine, write
the projection.

`costing_engine` is pure; this module feeds it and persists what it returns
into ``inventory_cost_layers``, ``inventory_cost_layer_consumptions``,
``inventory_line_costs`` and ``inventory_levels``. Three entry points:

* :func:`cost_posting` — called by ``inventory_service.post_transaction`` for
  every posting. Tries the engine's fast path from the current projection; when
  that refuses, replays the posting's whole warehouse (under the branch lock the
  poster already holds) and marks the warehouse for an estate replay, because a
  price learned here may need to cascade through transfers into other branches.
* :func:`replay_estate` — every warehouse at once, under every branch lock.
  Run by the scheduler for marked warehouses and nightly, and by the admin
  projection rebuild.
* :func:`replay` — the shared worker: any set of warehouses.

The projection's ids are deterministic (``costing_engine.layer_id``), so a
replay that reaches the same answer as the fast path writes nothing; only rows
whose figures changed are touched.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import bindparam, case, func, literal, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import utcnow
from app.models.branch import Branch
from app.models.inventory import (
    InventoryCostingDirty,
    InventoryCostingState,
    InventoryCostLayer,
    InventoryCostLayerConsumption,
    InventoryLevel,
    InventoryLineCost,
    InventoryTransaction,
    InventoryTransactionItem,
    InventoryTransactionTypeEnum,
    TransactionStatusEnum,
)
from app.services.inventory.costing_engine import (
    CostingEngine,
    LedgerLine,
    LineCostOut,
    NeedsReplay,
    Projection,
    SeedLayer,
    SeedState,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ReplayResult",
    "cost_posting",
    "replay",
    "replay_estate",
    "replay_marked_estate",
    "ledger_line_for",
    "cutover_sequence",
]

_CHUNK = 1000
_TRANSFER_SOURCES = ("transfer", "transfer_order")
_BATCH_INPUT_TYPES = (
    InventoryTransactionTypeEnum.CONSUMPTION_FROM_PRODUCTION.value,
    InventoryTransactionTypeEnum.WASTE_FROM_PRODUCTION.value,
)

_layers = InventoryCostLayer.__table__
_consumptions = InventoryCostLayerConsumption.__table__
_line_costs = InventoryLineCost.__table__


@dataclass(slots=True)
class ReplayResult:
    projection: Projection
    lines: int
    changes: int
    elapsed_ms: int


# ─── Reading the ledger ──────────────────────────────────────────────────────


def _storage_cost():
    """``line_cost_in_storage_unit`` in SQL: ingredient-entered cost × factor."""
    item = InventoryTransactionItem
    return case(
        (
            item.unit == "ingredient",
            item.unit_cost * func.coalesce(item.conversion_factor, 1),
        ),
        else_=item.unit_cost,
    )


def _ledger_columns():
    t, i = InventoryTransaction, InventoryTransactionItem
    return (
        i.id,
        t.id,
        i.item_id,
        t.warehouse_id,
        t.branch_id,
        t.posting_sequence,
        t.type,
        i.signed_quantity,
        _storage_cost(),
        t.posted_at,
        t.purchase_order_id,
        t.source_type,
        t.source_id,
        t.correction_group_id,
        t.order_id,
        t.reverses_transaction_id,
        i.reverses_line_id,
    )


def _row_to_line(row) -> LedgerLine:
    return LedgerLine(
        line_id=row[0],
        transaction_id=row[1],
        item_id=row[2],
        warehouse_id=row[3],
        branch_id=row[4],
        sequence=int(row[5]),
        type=row[6],
        delta=Decimal(str(row[7] or 0)),
        unit_cost=Decimal(str(row[8] or 0)),
        posted_at=row[9],
        purchase_order_id=row[10],
        source_type=row[11],
        source_id=row[12],
        correction_group_id=row[13],
        order_id=row[14],
        reverses_transaction_id=row[15],
        reverses_line_id=row[16],
    )


def ledger_line_for(
    transaction: InventoryTransaction,
    line: InventoryTransactionItem,
    *,
    warehouse_id: uuid.UUID,
    storage_cost: Decimal,
    posted_at,
) -> LedgerLine:
    """An in-flight (not yet closed) line, as the engine sees it."""
    return LedgerLine(
        line_id=line.id,
        transaction_id=transaction.id,
        item_id=line.item_id,
        warehouse_id=warehouse_id,
        branch_id=transaction.branch_id,
        sequence=int(transaction.posting_sequence),
        type=transaction.type,
        delta=Decimal(str(line.signed_quantity or 0)),
        unit_cost=Decimal(str(storage_cost or 0)),
        posted_at=posted_at,
        purchase_order_id=transaction.purchase_order_id,
        source_type=transaction.source_type,
        source_id=transaction.source_id,
        correction_group_id=transaction.correction_group_id,
        order_id=transaction.order_id,
        reverses_transaction_id=transaction.reverses_transaction_id,
        reverses_line_id=line.reverses_line_id,
    )


async def _closed_lines(
    db: AsyncSession, warehouse_ids: set[uuid.UUID] | None
) -> list[LedgerLine]:
    t, i = InventoryTransaction, InventoryTransactionItem
    stmt = (
        select(*_ledger_columns())
        .join(i, i.transaction_id == t.id)
        .where(t.status == TransactionStatusEnum.CLOSED.value)
        .order_by(t.posting_sequence, i.id)
    )
    if warehouse_ids is not None:
        stmt = stmt.where(t.warehouse_id.in_(warehouse_ids))
    return [_row_to_line(row) for row in (await db.execute(stmt)).all()]


async def _reversed_transactions(db: AsyncSession) -> set[uuid.UUID]:
    rows = await db.execute(
        select(InventoryTransaction.reverses_transaction_id).where(
            InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
            InventoryTransaction.reverses_transaction_id.is_not(None),
        )
    )
    return {row[0] for row in rows.all()}


async def _po_prices(
    db: AsyncSession, reversed_: set[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[int, Decimal]]]:
    """Priced purchase-order receipts, estate-wide — the estimate of last resort."""
    t, i = InventoryTransaction, InventoryTransactionItem
    rows = await db.execute(
        select(i.item_id, t.posting_sequence, _storage_cost(), t.id).where(
            i.transaction_id == t.id,
            t.status == TransactionStatusEnum.CLOSED.value,
            t.type == InventoryTransactionTypeEnum.PURCHASING.value,
            t.purchase_order_id.is_not(None),
            i.unit_cost > 0,
        )
    )
    prices: dict[uuid.UUID, list[tuple[int, Decimal]]] = defaultdict(list)
    for item_id, sequence, cost, transaction_id in rows.all():
        if transaction_id in reversed_:
            continue
        prices[item_id].append((int(sequence), Decimal(str(cost))))
    return prices


async def _external_sends(
    db: AsyncSession,
    lines: list[LedgerLine],
    scope: set[uuid.UUID] | None,
) -> dict[tuple, tuple[Decimal, Decimal, bool]]:
    """Projected cost of transfer sends posted outside the replayed warehouses."""
    wanted = {
        (line.source_id, line.item_id)
        for line in lines
        if line.type == InventoryTransactionTypeEnum.TRANSFER_RECEIVE.value
        and line.source_type in _TRANSFER_SOURCES
        and line.source_id
    }
    if not wanted:
        return {}
    t, i, c = (
        InventoryTransaction,
        InventoryTransactionItem,
        InventoryCostLayerConsumption,
    )
    stmt = (
        select(
            t.source_id,
            i.item_id,
            func.sum(c.quantity),
            func.sum(c.total_cost),
            func.bool_or(c.cost_is_provisional),
        )
        .join(i, i.transaction_id == t.id)
        .join(c, c.consuming_line_id == i.id)
        .where(
            t.status == TransactionStatusEnum.CLOSED.value,
            t.type == InventoryTransactionTypeEnum.TRANSFER_SEND.value,
            t.source_type.in_(_TRANSFER_SOURCES),
            t.source_id.in_({source for source, _ in wanted}),
            t.reverses_transaction_id.is_(None),
        )
        .group_by(t.source_id, i.item_id)
    )
    if scope is not None:
        stmt = stmt.where(t.warehouse_id.not_in(scope))
    out: dict[tuple, tuple[Decimal, Decimal, bool]] = {}
    for source_id, item_id, qty, total, provisional in (await db.execute(stmt)).all():
        if (source_id, item_id) in wanted:
            out[("transfer", source_id, item_id)] = (
                Decimal(str(qty or 0)),
                Decimal(str(total or 0)),
                bool(provisional),
            )
    return out


async def cutover_sequence(db: AsyncSession) -> int | None:
    state = await db.get(InventoryCostingState, True)
    return state.cutover_sequence if state else None


# ─── Writing the projection ──────────────────────────────────────────────────


def _layer_row(layer) -> dict:
    return {
        "id": layer.id,
        "item_id": layer.item_id,
        "warehouse_id": layer.warehouse_id,
        "branch_id": layer.branch_id,
        "source_transaction_id": layer.source_transaction_id,
        "source_line_id": layer.source_line_id,
        "purchase_order_id": layer.purchase_order_id,
        "source_kind": layer.source_kind,
        "posting_sequence": layer.posting_sequence,
        "layer_index": layer.layer_index,
        "original_quantity": layer.original_quantity,
        "remaining_quantity": layer.remaining_quantity,
        "unit_cost": layer.unit_cost,
        "cost_is_provisional": layer.cost_is_provisional,
        "priced_by_line_id": layer.priced_by_line_id,
        "received_at": layer.received_at or utcnow(),
        "exhausted_at": layer.exhausted_at,
    }


def _consumption_row(row) -> dict:
    return {
        "id": row.id,
        "consuming_line_id": row.consuming_line_id,
        "layer_id": row.layer_id,
        "item_id": row.item_id,
        "warehouse_id": row.warehouse_id,
        "quantity": row.quantity,
        "unit_cost": row.unit_cost,
        "total_cost": row.total_cost,
        "posting_sequence": row.posting_sequence,
        "is_shortfall": row.is_shortfall,
        "cost_is_provisional": row.cost_is_provisional,
        "priced_by_line_id": row.priced_by_line_id,
    }


def _line_cost_row(row: LineCostOut) -> dict:
    return {
        "line_id": row.line_id,
        "transaction_id": row.transaction_id,
        "item_id": row.item_id,
        "warehouse_id": row.warehouse_id,
        "branch_id": row.branch_id,
        "posting_sequence": row.posting_sequence,
        "quantity": row.quantity,
        "unit_cost": row.unit_cost,
        "total_cost": row.total_cost,
        "booked_unit_cost": row.booked_unit_cost,
        "is_provisional": row.is_provisional,
        "priced_by_line_id": row.priced_by_line_id,
        "running_quantity": row.running_quantity,
        "running_value": row.running_value,
        "superseded": row.superseded,
    }


_LAYER_COMPARED = (
    "remaining_quantity",
    "original_quantity",
    "unit_cost",
    "cost_is_provisional",
    "priced_by_line_id",
    "exhausted_at",
    "source_kind",
    "posting_sequence",
)
_CONSUMPTION_COMPARED = (
    "layer_id",
    "quantity",
    "unit_cost",
    "total_cost",
    "is_shortfall",
    "cost_is_provisional",
    "priced_by_line_id",
    "posting_sequence",
)
_LINE_COST_COMPARED = (
    "quantity",
    "unit_cost",
    "total_cost",
    "booked_unit_cost",
    "is_provisional",
    "priced_by_line_id",
    "running_quantity",
    "running_value",
    "superseded",
)


def _same(a, b) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return Decimal(str(a or 0)) == Decimal(str(b or 0))
    return a == b


async def _existing(db: AsyncSession, table, key, columns, where) -> dict:
    rows = await db.execute(
        select(table.c[key], *[table.c[c] for c in columns]).where(where)
    )
    return {row[0]: dict(zip(columns, row[1:])) for row in rows.all()}


def _diff(desired: dict[uuid.UUID, dict], existing: dict, compared) -> tuple:
    inserts, updates = [], []
    for key, row in desired.items():
        current = existing.get(key)
        if current is None:
            inserts.append(row)
        elif any(not _same(current[c], row[c]) for c in compared):
            updates.append(row)
    deletes = [key for key in existing if key not in desired]
    return inserts, updates, deletes


async def _executemany_update(db: AsyncSession, table, key: str, columns, rows) -> None:
    if not rows:
        return
    stmt = (
        table.update()
        .where(table.c[key] == bindparam(f"b_{key}"))
        .values({c: bindparam(f"b_{c}") for c in columns})
    )
    for start in range(0, len(rows), _CHUNK):
        chunk = rows[start : start + _CHUNK]
        await db.execute(
            stmt,
            [
                {f"b_{k}": v for k, v in row.items() if k == key or k in columns}
                for row in chunk
            ],
        )


async def _insert(db: AsyncSession, table, rows) -> None:
    for start in range(0, len(rows), _CHUNK):
        await db.execute(table.insert(), rows[start : start + _CHUNK])


async def _delete(db: AsyncSession, table, key: str, ids) -> None:
    ids = list(ids)
    for start in range(0, len(ids), _CHUNK):
        await db.execute(
            table.delete().where(table.c[key].in_(ids[start : start + _CHUNK]))
        )


async def _write_projection(
    db: AsyncSession, projection: Projection, scope: set[uuid.UUID] | None
) -> int:
    """Make the stored projection for *scope* (None = every warehouse) match."""
    in_scope_layers = (
        _layers.c.warehouse_id.in_(scope) if scope is not None else literal(True)
    )
    in_scope_consumptions = (
        _consumptions.c.warehouse_id.in_(scope) if scope is not None else literal(True)
    )
    in_scope_lines = (
        _line_costs.c.warehouse_id.in_(scope) if scope is not None else literal(True)
    )
    layer_columns = (
        tuple(c for c in _layer_row(projection.layers[0]) if c != "id")
        if projection.layers
        else ()
    )
    desired_layers = {layer.id: _layer_row(layer) for layer in projection.layers}
    desired_consumptions = {
        row.id: _consumption_row(row) for row in projection.consumptions
    }
    desired_lines = {
        row.line_id: _line_cost_row(row) for row in projection.line_costs.values()
    }

    existing_layers = await _existing(
        db, _layers, "id", _LAYER_COMPARED, in_scope_layers
    )
    existing_consumptions = await _existing(
        db, _consumptions, "id", _CONSUMPTION_COMPARED, in_scope_consumptions
    )
    existing_lines = await _existing(
        db, _line_costs, "line_id", _LINE_COST_COMPARED, in_scope_lines
    )

    layer_ins, layer_upd, layer_del = _diff(
        desired_layers, existing_layers, _LAYER_COMPARED
    )
    cons_ins, cons_upd, cons_del = _diff(
        desired_consumptions, existing_consumptions, _CONSUMPTION_COMPARED
    )
    line_ins, line_upd, line_del = _diff(
        desired_lines, existing_lines, _LINE_COST_COMPARED
    )

    # FK order: consumptions point at layers.
    await _delete(db, _consumptions, "id", cons_del)
    await _insert(db, _layers, layer_ins)
    await _executemany_update(
        db, _layers, "id", layer_columns or _LAYER_COMPARED, layer_upd
    )
    await _insert(db, _consumptions, cons_ins)
    await _executemany_update(
        db,
        _consumptions,
        "id",
        tuple(c for c in _CONSUMPTION_COMPARED),
        cons_upd,
    )
    await _delete(db, _layers, "id", layer_del)
    await _delete(db, _line_costs, "line_id", line_del)
    await _insert(db, _line_costs, line_ins)
    await _executemany_update(db, _line_costs, "line_id", _LINE_COST_COMPARED, line_upd)

    changes = (
        len(layer_ins)
        + len(layer_upd)
        + len(layer_del)
        + len(cons_ins)
        + len(cons_upd)
        + len(cons_del)
        + len(line_ins)
        + len(line_upd)
        + len(line_del)
    )
    changes += await _write_levels(db, projection, scope)
    return changes


async def _write_levels(
    db: AsyncSession, projection: Projection, scope: set[uuid.UUID] | None
) -> int:
    stmt = select(InventoryLevel)
    if scope is not None:
        stmt = stmt.where(InventoryLevel.warehouse_id.in_(scope))
    levels = {
        (level.item_id, level.warehouse_id): level
        for level in (await db.execute(stmt)).scalars().all()
    }
    changed = 0
    now = utcnow()
    for key, out in projection.levels.items():
        level = levels.get(key)
        if level is None:
            level = InventoryLevel(
                item_id=out.item_id,
                warehouse_id=out.warehouse_id,
                quantity=out.quantity,
                average_cost=out.average_cost,
                projected_through_sequence=out.through_sequence,
                reconciled_at=now,
            )
            db.add(level)
            changed += 1
        elif not _same(level.quantity, out.quantity) or not _same(
            level.average_cost, out.average_cost
        ):
            level.quantity = out.quantity
            level.average_cost = out.average_cost
            level.projected_through_sequence = out.through_sequence
            level.reconciled_at = now
            changed += 1
    await db.flush()
    return changed


# ─── Replays ─────────────────────────────────────────────────────────────────


async def replay(
    db: AsyncSession,
    *,
    warehouse_ids: set[uuid.UUID] | None,
    in_flight: list[LedgerLine] | None = None,
    write: bool = True,
) -> ReplayResult:
    """Replay the ledger for *warehouse_ids* (None = all) and store the result.

    ``in_flight`` are lines of the transaction being posted right now — not yet
    closed, so not in the query, but part of the history being costed. The
    caller must hold the branch lock of every warehouse in scope.
    """
    started = time.monotonic()
    lines = await _closed_lines(db, warehouse_ids)
    extra = [
        line
        for line in in_flight or []
        if line.line_id not in {row.line_id for row in lines}
    ]
    lines.extend(extra)
    lines.sort(key=lambda line: line.sort_key)
    reversed_ = await _reversed_transactions(db)
    reversed_.update(
        line.reverses_transaction_id for line in extra if line.reverses_transaction_id
    )
    engine = CostingEngine(
        cutover_sequence=await cutover_sequence(db),
        reversed_transactions=reversed_,
        po_prices=await _po_prices(db, reversed_),
        external_sends=await _external_sends(db, lines, warehouse_ids),
    )
    for line in lines:
        engine.apply(line)
    projection = engine.project()
    changes = await _write_projection(db, projection, warehouse_ids) if write else 0
    return ReplayResult(
        projection=projection,
        lines=len(lines),
        changes=changes,
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )


async def _lock_every_branch(db: AsyncSession, *, wait: bool) -> bool:
    """Take every branch's inventory lock, in a fixed order.

    ``wait=False`` (the scheduler) gives up on the first busy branch — the
    caller rolls back to release what it took and retries next tick, so it can
    never deadlock with a posting that holds one branch and wants another.
    """
    branch_ids = sorted((await db.execute(select(Branch.id))).scalars().all(), key=str)
    for branch_id in branch_ids:
        key = func.hashtextextended(f"inventory:{branch_id}", 0)
        if wait:
            await db.execute(select(func.pg_advisory_xact_lock(key)))
        elif not await db.scalar(select(func.pg_try_advisory_xact_lock(key))):
            return False
    return True


async def replay_estate(
    db: AsyncSession, *, wait: bool = True, write: bool = True
) -> ReplayResult | None:
    """Replay every warehouse. Returns None if a branch was busy (``wait=False``)."""
    if not await _lock_every_branch(db, wait=wait):
        return None
    result = await replay(db, warehouse_ids=None, write=write)
    if write:
        await db.execute(InventoryCostingDirty.__table__.delete())
        state = await db.get(InventoryCostingState, True)
        if state is None:
            state = InventoryCostingState(id=True)
            db.add(state)
        state.last_estate_replay_at = utcnow()
        state.last_estate_replay_ms = result.elapsed_ms
        state.last_estate_changes = result.changes
        await db.flush()
    return result


async def replay_marked_estate(db: AsyncSession) -> ReplayResult | None:
    """The scheduler's tick: replay the estate if any warehouse asked for it."""
    marked = await db.scalar(select(func.count()).select_from(InventoryCostingDirty))
    if not marked:
        return None
    await db.execute(text("SET LOCAL lock_timeout = '5s'"))
    return await replay_estate(db, wait=False)


async def _mark_dirty(db: AsyncSession, warehouse_id: uuid.UUID) -> None:
    await db.execute(
        pg_insert(InventoryCostingDirty.__table__)
        .values(warehouse_id=warehouse_id, dirty_at=utcnow())
        .on_conflict_do_nothing(index_elements=["warehouse_id"])
    )


# ─── The posting path ────────────────────────────────────────────────────────


async def _seeds(
    db: AsyncSession,
    keys: set[tuple[uuid.UUID, uuid.UUID]],
    in_flight: list[LedgerLine],
    levels: dict[tuple[uuid.UUID, uuid.UUID], InventoryLevel],
) -> list[SeedState]:
    moved: dict[tuple, Decimal] = defaultdict(Decimal)
    for line in in_flight:
        moved[(line.item_id, line.warehouse_id)] += line.delta
    item_ids = {item for item, _ in keys}
    warehouse_ids = {wh for _, wh in keys}
    layer_rows = (
        await db.execute(
            # Columns, not entities: the rows are rewritten below through Core,
            # and ORM instances left in the identity map would go stale.
            select(
                _layers.c.item_id,
                _layers.c.warehouse_id,
                _layers.c.source_line_id,
                _layers.c.layer_index,
                _layers.c.source_transaction_id,
                _layers.c.purchase_order_id,
                _layers.c.source_kind,
                _layers.c.posting_sequence,
                _layers.c.original_quantity,
                _layers.c.remaining_quantity,
                _layers.c.unit_cost,
                _layers.c.priced_by_line_id,
                _layers.c.received_at,
                _layers.c.cost_is_provisional,
            ).where(
                _layers.c.item_id.in_(item_ids),
                _layers.c.warehouse_id.in_(warehouse_ids),
                _layers.c.remaining_quantity > 0,
            )
        )
    ).all()
    by_key: dict[tuple, list[SeedLayer]] = defaultdict(list)
    for row in layer_rows:
        by_key[(row.item_id, row.warehouse_id)].append(
            SeedLayer(
                line_id=row.source_line_id,
                index=row.layer_index,
                transaction_id=row.source_transaction_id,
                purchase_order_id=row.purchase_order_id,
                source_kind=row.source_kind,
                sequence=int(row.posting_sequence),
                original=Decimal(str(row.original_quantity)),
                remaining=Decimal(str(row.remaining_quantity)),
                unit_cost=Decimal(str(row.unit_cost)),
                priced_by=row.priced_by_line_id,
                received_at=row.received_at,
                provisional=bool(row.cost_is_provisional),
            )
        )
    provisional_keys = set()
    for table in (_layers, _consumptions):
        rows = await db.execute(
            select(table.c.item_id, table.c.warehouse_id)
            .where(
                table.c.item_id.in_(item_ids),
                table.c.warehouse_id.in_(warehouse_ids),
                table.c.cost_is_provisional.is_(True),
            )
            .distinct()
        )
        provisional_keys.update((row[0], row[1]) for row in rows.all())
    seeds = []
    for key in keys:
        level = levels.get(key)
        quantity_now = Decimal(str(level.quantity if level else 0))
        seeds.append(
            SeedState(
                item_id=key[0],
                warehouse_id=key[1],
                branch_id=in_flight[0].branch_id,
                quantity=quantity_now - moved[key],
                layers=by_key.get(key, []),
                has_open_pending=key in provisional_keys,
                last_cost=Decimal(str(level.average_cost if level else 0)),
            )
        )
    return seeds


async def _group_inputs(
    db: AsyncSession, in_flight: list[LedgerLine]
) -> dict[uuid.UUID, list[tuple[Decimal, Decimal, bool]]]:
    groups = {
        line.correction_group_id
        for line in in_flight
        if line.type == InventoryTransactionTypeEnum.PRODUCTION.value
        and line.correction_group_id is not None
    }
    if not groups:
        return {}
    t, i, c = (
        InventoryTransaction,
        InventoryTransactionItem,
        InventoryCostLayerConsumption,
    )
    rows = await db.execute(
        select(t.correction_group_id, c.quantity, c.total_cost, c.cost_is_provisional)
        .join(i, i.transaction_id == t.id)
        .join(c, c.consuming_line_id == i.id)
        .where(
            t.correction_group_id.in_(groups),
            t.status == TransactionStatusEnum.CLOSED.value,
            t.type.in_(_BATCH_INPUT_TYPES),
            t.reverses_transaction_id.is_(None),
        )
    )
    out: dict[uuid.UUID, list[tuple[Decimal, Decimal, bool]]] = defaultdict(list)
    for group, qty, total, provisional in rows.all():
        out[group].append((Decimal(str(qty)), Decimal(str(total)), bool(provisional)))
    return out


async def _write_fast(
    db: AsyncSession,
    projection: Projection,
    levels: dict[tuple[uuid.UUID, uuid.UUID], InventoryLevel],
) -> None:
    """Store a fast-path result: new rows, and the seeded layers it drew from."""
    await _insert(
        db, _layers, [_layer_row(r) for r in projection.layers if not r.seeded]
    )
    await _executemany_update(
        db,
        _layers,
        "id",
        ("remaining_quantity", "exhausted_at"),
        [_layer_row(r) for r in projection.layers if r.seeded and r.touched],
    )
    await _insert(
        db, _consumptions, [_consumption_row(r) for r in projection.consumptions]
    )
    await _insert(
        db, _line_costs, [_line_cost_row(r) for r in projection.line_costs.values()]
    )
    for key, out in projection.levels.items():
        level = levels.get(key)
        if level is not None:
            level.average_cost = out.average_cost
    await db.flush()


async def cost_posting(
    db: AsyncSession,
    *,
    lines: list[LedgerLine],
    levels: dict[tuple[uuid.UUID, uuid.UUID], InventoryLevel],
) -> dict[uuid.UUID, LineCostOut]:
    """Cost one posting's lines and store the projection. Returns line costs.

    ``levels`` are the level rows the poster already locked and moved, keyed by
    (item, warehouse). The fast path starts from the stored projection; if the
    engine refuses, the posting's warehouse is replayed from scratch.
    """
    if not lines:
        return {}
    await db.flush()
    keys = {(line.item_id, line.warehouse_id) for line in lines}
    engine = CostingEngine(
        cutover_sequence=await cutover_sequence(db),
        fast=True,
        seeds=await _seeds(db, keys, lines, levels),
        external_group_inputs=await _group_inputs(db, lines),
        external_sends=await _external_sends(db, lines, {lines[0].warehouse_id}),
    )
    try:
        for line in sorted(lines, key=lambda row: row.sort_key):
            engine.apply(line)
        projection = engine.project()
    except NeedsReplay as reason:
        warehouse_id = lines[0].warehouse_id
        result = await replay(db, warehouse_ids={warehouse_id}, in_flight=lines)
        await _mark_dirty(db, warehouse_id)
        logger.info(
            "costing: replayed warehouse %s (%s lines, %s changes, %sms): %s",
            warehouse_id,
            result.lines,
            result.changes,
            result.elapsed_ms,
            reason,
        )
        return {
            line.line_id: result.projection.line_costs[line.line_id] for line in lines
        }
    await _write_fast(db, projection, levels)
    return {line.line_id: projection.line_costs[line.line_id] for line in lines}
