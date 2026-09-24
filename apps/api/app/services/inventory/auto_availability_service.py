"""Auto off-sale from produced-good stock (experimental; Barsha pilot).

When a branch's stock of a ``produced_good`` cannot cover one sale (on-hand below
what one unit of the owner's recipe draws, and always at ≤ 0), every product and
modifier option in that position is taken off sale at that branch, and put back
when the stock recovers. A "9 Pieces" box option goes off with 5 brownies left
while "3 Pieces" stays on. Only branches with
``branch_inventory_settings.auto_availability_enabled`` take part.

**Trigger.** ``inventory_service.post_transaction`` — the one level writer —
upserts ``inventory_availability_dirty(branch, item)`` for produced goods at an
enabled branch, in the same transaction as the movement (``mark_dirty``). Recipe
activation and ``reconcile_levels(apply=True)`` mark too. Nothing is evaluated
inline: an order close or a counter sale pays one upsert, never this module.

**Drain.** ``run_forever`` wakes every 30 s under its own advisory lock (on
``held_session`` — one scheduler connection, not two), evaluates the branches
with dirty rows, and every 15 min re-evaluates every enabled branch in full as
the safety net for any level change that never marked (the estate costing
replay, a deploy). Each branch commits on its own, so one bad branch cannot
roll back another's.

**The rules** (``decide``), per product/option row at the branch:

=================================  ========  =====================================
Current row                        Desired   Action
=================================  ========  =====================================
on sale, no override               off       auto-off (source 'auto', auto_state)
source 'auto' (off)                on        auto-on (stock recovered / removed
                                             from recipe)
``staff_override_until_restock``   —         skip; clear the flag once no trigger
                                             item is short
source 'staff' (off)               —         never touched
=================================  ========  =====================================

*Desired off* = any produced-good leaf of the owner's expanded active recipe has
branch on-hand ≤ 0 or below what one sale of the owner draws
(``requirements_by_owner``, in storage units). Products with ``consumes_stock=False`` draw nothing through
their own recipe and are skipped (their options still count — the same rule
``recipe_service.snapshot_order`` applies). Raw materials, packaging and every
other kind are ignored. Turning the branch flag off releases every 'auto' row
there (reason ``feature_disabled``).

Every write goes through ``availability_service`` with ``source='auto'`` and
``SYSTEM_ACTOR``, so each is audited. After commit — outside the lock and off its
connection — the catalogue cache is retired, GrubOps is told (the reconcile loop
remains the authority), and the owner gets one email per branch per batch.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, delete, false, func, or_, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import advisory_lock, heartbeat
from app.core.exceptions import AppError
from app.models.branch import Branch
from app.models.inventory import (
    InventoryItem,
    InventoryLevel,
    InventoryTransaction,
    InventoryTransactionItem,
    TransactionStatusEnum,
    Warehouse,
)
from app.models.inventory_v2 import (
    BranchInventorySettings,
    InventoryAvailabilityDirty,
    InventoryItemKindEnum,
    RecipeOwnerKindEnum,
)
from app.models.menu import BranchModifierOption, BranchProduct
from app.models.modifier import Modifier, ModifierOption, ProductModifier
from app.models.order import Order
from app.models.product import Product
from app.models.user import User
from app.services.catalog import availability_service as availability
from app.services.inventory import recipe_service

logger = logging.getLogger(__name__)

__all__ = [
    "BranchReport",
    "Change",
    "Decision",
    "decide",
    "evaluate_branch",
    "leaves_by_owner",
    "mark_branch_dirty",
    "mark_dirty",
    "mark_items_at_enabled_branches",
    "owners_sold_at",
    "publish",
    "release_disabled_branches",
    "run_forever",
    "tick",
]

#: Same flat 64-bit namespace as every other advisory lock: "mmBATCH" + 0E, the
#: next free slot after the costing estate sweeper's 0D.
_ADVISORY_LOCK_KEY = 0x6D6D_4241_5443_480E

_TICK_SECONDS = 30
_FULL_SWEEP_SECONDS = 15 * 60

_PRODUCED_GOOD = InventoryItemKindEnum.PRODUCED_GOOD.value
_PRODUCT = RecipeOwnerKindEnum.PRODUCT.value
_OPTION = RecipeOwnerKindEnum.MODIFIER_OPTION.value

_dirty = InventoryAvailabilityDirty.__table__


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── Marking (called from the posting path — keep it one statement) ──────────


async def mark_dirty(
    db: AsyncSession,
    *,
    branch_id: uuid.UUID,
    item_ids: Iterable[uuid.UUID],
    transaction_id: uuid.UUID | None = None,
) -> None:
    """Upsert (branch, item) marks. The caller has already filtered to produced
    goods at an enabled branch — this is the one statement `post_transaction`
    pays, and it commits atomically with the movement."""
    ids = sorted(set(item_ids), key=str)
    if not ids:
        return
    stmt = pg_insert(_dirty).values(
        [
            {"branch_id": branch_id, "item_id": item_id, "last_txn_id": transaction_id}
            for item_id in ids
        ]
    )
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["branch_id", "item_id"],
            set_={
                "last_txn_id": stmt.excluded.last_txn_id,
                "marked_at": func.clock_timestamp(),
            },
        )
    )


async def mark_items_at_enabled_branches(
    db: AsyncSession,
    *,
    item_ids: Iterable[uuid.UUID] | None,
    branch_id: uuid.UUID | None = None,
) -> None:
    """Mark the produced goods among `item_ids` (all, when None) at every enabled
    branch (or just `branch_id`) — one INSERT … SELECT. For recipe activation,
    a level reconcile, and switching the flag on."""
    selected = (
        select(BranchInventorySettings.branch_id, InventoryItem.id)
        .join(InventoryItem, true())
        .where(
            BranchInventorySettings.auto_availability_enabled.is_(True),
            InventoryItem.kind == _PRODUCED_GOOD,
        )
    )
    if item_ids is not None:
        ids = list(set(item_ids))
        if not ids:
            return
        selected = selected.where(InventoryItem.id.in_(ids))
    if branch_id is not None:
        selected = selected.where(BranchInventorySettings.branch_id == branch_id)
    stmt = pg_insert(_dirty).from_select(["branch_id", "item_id"], selected)
    await db.execute(
        stmt.on_conflict_do_update(
            index_elements=["branch_id", "item_id"],
            set_={"marked_at": func.clock_timestamp()},
        )
    )


async def mark_branch_dirty(db: AsyncSession, branch_id: uuid.UUID) -> None:
    """Every produced good at one (enabled) branch — the flag was just switched
    on, so the next tick evaluates the whole branch rather than waiting for the
    15-minute sweep."""
    await mark_items_at_enabled_branches(db, item_ids=None, branch_id=branch_id)


async def _any_branch_enabled(db: AsyncSession) -> bool:
    return bool(
        await db.scalar(
            select(
                select(BranchInventorySettings.id)
                .where(BranchInventorySettings.auto_availability_enabled.is_(True))
                .exists()
            )
        )
    )


async def mark_recipe_change(db: AsyncSession, versions: Iterable) -> None:
    """A recipe version was activated (and its predecessor retired).

    Marks the produced goods either version reaches — directly or through a
    phantom sub-recipe — at every enabled branch, so an owner whose new recipe
    *dropped* a produced good is re-evaluated and released ("removed from
    recipe"). Nothing to do, and nothing loaded, while no branch has the flag on.
    """
    versions = [v for v in versions if v is not None]
    if not versions or not await _any_branch_enabled(db):
        return
    catalog = await recipe_service.load_active_catalog(db)
    item_ids: set[uuid.UUID] = set()
    for version in versions:
        item_ids.update(line.item_id for line in version.lines)
        try:
            expanded = recipe_service.expand_lines(
                catalog,
                owner_kind="recipe_version",
                owner_id=None,
                basis=version.basis,
                batch_yield=version.batch_yield,
                lines=version.lines,
            )
        except AppError:
            # A retired version can reference a phantom whose recipe has since
            # gone; its direct lines are still marked above.
            continue
        item_ids.update(expanded)
    await mark_items_at_enabled_branches(db, item_ids=item_ids)


# ─── The decision (pure) ──────────────────────────────────────────────────────


def leaves_by_owner(
    catalog: recipe_service.ActiveRecipeCatalog,
) -> dict[tuple[str, uuid.UUID], frozenset[uuid.UUID] | None]:
    """Every product/option recipe → the produced goods it draws, as plain ids.

    Expanded through phantom sub-recipes by the one expansion rule
    (`recipe_service.expand_lines`). `None` marks an owner whose recipe cannot
    be expanded right now (a phantom with no active recipe, a cycle): the
    evaluator leaves such an owner exactly as it is rather than guess.

    Plain data on purpose — the tick commits per branch, and ORM objects from
    the catalogue would expire on a rollback mid-tick.
    """
    out: dict[tuple[str, uuid.UUID], frozenset[uuid.UUID] | None] = {}
    for (kind, owner_id), version in catalog.versions.items():
        if kind not in (_PRODUCT, _OPTION):
            continue
        try:
            expanded = recipe_service.expand_lines(
                catalog,
                owner_kind=kind,
                owner_id=owner_id,
                basis=version.basis,
                batch_yield=version.batch_yield,
                lines=version.lines,
            )
        except AppError:
            out[(kind, owner_id)] = None
            continue
        out[(kind, owner_id)] = frozenset(
            item_id
            for item_id in expanded
            if (item := catalog.items.get(item_id)) is not None
            and item.kind == _PRODUCED_GOOD
        )
    return out


def requirements_by_owner(
    catalog: recipe_service.ActiveRecipeCatalog,
) -> dict[tuple[str, uuid.UUID], dict[uuid.UUID, Decimal]]:
    """Every product/option recipe → how much of each produced good ONE sale of
    it takes, in the storage units stock is counted in.

    A "9 Pieces" box option draws 9 brownies: with 5 on the shelf it cannot be
    sold even though stock is above zero. Owners whose recipe cannot be
    expanded are left out (their leaves are None and the evaluator skips them).
    """
    out: dict[tuple[str, uuid.UUID], dict[uuid.UUID, Decimal]] = {}
    for (kind, owner_id), version in catalog.versions.items():
        if kind not in (_PRODUCT, _OPTION):
            continue
        try:
            expanded = recipe_service.expand_lines(
                catalog,
                owner_kind=kind,
                owner_id=owner_id,
                basis=version.basis,
                batch_yield=version.batch_yield,
                lines=version.lines,
            )
        except AppError:
            continue
        needs: dict[uuid.UUID, Decimal] = {}
        for item_id, line in expanded.items():
            item = catalog.items.get(item_id)
            if item is None or item.kind != _PRODUCED_GOOD:
                continue
            factor = Decimal(str(item.storage_to_ingredient_factor or 1)) or Decimal(1)
            needs[item_id] = Decimal(str(line.quantity)) / factor
        out[(kind, owner_id)] = needs
    return out


@dataclass(frozen=True)
class Decision:
    """What to do with one row. `triggers` are the items behind it: the ones at
    short of one sale for an off, the ones that took it off (from `auto_state`)
    for an on."""

    action: str  # "off" | "on" | "clear_override"
    reason: str | None = None
    triggers: tuple[uuid.UUID, ...] = ()


def _trigger_ids(auto_state) -> set[uuid.UUID]:
    ids: set[uuid.UUID] = set()
    for entry in (auto_state or {}).get("items") or []:
        try:
            ids.add(uuid.UUID(str(entry.get("item_id"))))
        except (TypeError, ValueError, AttributeError):
            continue
    return ids


def decide(
    row,
    *,
    sold: bool,
    leaves: frozenset[uuid.UUID] | None,
    on_hand: Mapping[uuid.UUID, Decimal],
    now: datetime | None = None,
    required: Mapping[uuid.UUID, Decimal] | None = None,
) -> Decision | None:
    """The decision table, for one product or option row at one branch.

    `row` is the `BranchProduct`/`BranchModifierOption` (None when the branch
    has no exception row — on sale); `sold` whether the branch sells the owner;
    `leaves` its produced-good leaves (None: cannot tell, leave it alone);
    `on_hand` branch stock by item (missing = 0).
    """
    if leaves is None:
        return None

    def short(item_id: uuid.UUID) -> bool:
        have = on_hand.get(item_id, Decimal(0))
        # Short of what one sale takes (a 9-piece box with 5 left), and always
        # at or below zero whatever the recipe says.
        need = (required or {}).get(item_id, Decimal(0))
        return have <= 0 or have < need

    depleted = tuple(sorted((i for i in leaves if short(i)), key=str)) if sold else ()

    if row is not None and row.staff_override_until_restock:
        # Staff wins until restock: hands off while anything is still at zero.
        return None if depleted else Decision("clear_override")

    source = availability.effective_unavailable_source(row, now)
    if source == availability.SOURCE_STAFF:
        return None  # a person took it off; only a person puts it back
    if source == availability.SOURCE_AUTO:
        if depleted:
            return None  # still out — stays off
        previous = _trigger_ids(row.auto_state)
        reason = (
            availability.REASON_STOCK_RECOVERED
            if sold and previous & leaves
            else availability.REASON_REMOVED_FROM_RECIPE
        )
        return Decision("on", reason, tuple(sorted(previous, key=str)))

    # On sale (no row, an in-stock row, or a lapsed stockout).
    if depleted and getattr(row, "is_active", True) is not False:
        return Decision("off", availability.REASON_STOCK_DEPLETED, depleted)
    return None


# ─── Evaluation (one branch, one transaction) ────────────────────────────────


@dataclass
class Change:
    """One applied on/off-sale change, as the email and the pushes need it."""

    owner_kind: str  # "product" | "modifier_option"
    owner_id: uuid.UUID
    name: str
    in_stock: bool
    reason: str
    trigger_item_ids: tuple[uuid.UUID, ...]


@dataclass
class BranchReport:
    branch_id: uuid.UUID
    branch_name: str
    branch_reference: str | None
    changes: list[Change] = field(default_factory=list)
    #: Rendered for `email_service.send_auto_availability_change`, built inside
    #: the transaction so the email is sent after commit without the session.
    email_rows: list[dict] = field(default_factory=list)


async def _branch_on_hand(
    db: AsyncSession, branch_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """SUM(level) over the branch's live warehouses. A missing level is 0."""
    ids = list(set(item_ids))
    if not ids:
        return {}
    rows = await db.execute(
        select(InventoryLevel.item_id, func.sum(InventoryLevel.quantity))
        .join(Warehouse, Warehouse.id == InventoryLevel.warehouse_id)
        .where(
            Warehouse.branch_id == branch_id,
            Warehouse.deleted_at.is_(None),
            InventoryLevel.item_id.in_(ids),
        )
        .group_by(InventoryLevel.item_id)
    )
    return {item_id: Decimal(str(total or 0)) for item_id, total in rows.all()}


async def _latest_movements(
    db: AsyncSession, branch_id: uuid.UUID, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, dict]:
    """The last closed ledger line per item at the branch — what moved it."""
    ids = list(set(item_ids))
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(
                InventoryTransactionItem.item_id,
                InventoryTransactionItem.signed_quantity,
                InventoryTransaction.id,
                InventoryTransaction.type,
                InventoryTransaction.reference,
                InventoryTransaction.posted_at,
                InventoryTransaction.purchase_order_id,
                Order.order_number,
                User.display_name,
                User.email,
            )
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryTransactionItem.transaction_id,
            )
            .outerjoin(Order, Order.id == InventoryTransaction.order_id)
            .outerjoin(
                User,
                User.id
                == func.coalesce(
                    InventoryTransaction.poster_id, InventoryTransaction.creator_id
                ),
            )
            .where(
                InventoryTransaction.branch_id == branch_id,
                InventoryTransaction.status == TransactionStatusEnum.CLOSED.value,
                InventoryTransactionItem.item_id.in_(ids),
            )
            .order_by(
                InventoryTransactionItem.item_id,
                InventoryTransaction.posting_sequence.desc().nulls_last(),
                InventoryTransaction.posted_at.desc().nulls_last(),
            )
            .distinct(InventoryTransactionItem.item_id)
        )
    ).all()
    return {
        row.item_id: {
            "transaction_id": str(row.id),
            "type": row.type,
            "reference": row.reference,
            "quantity": Decimal(str(row.signed_quantity or 0)),
            "at": row.posted_at,
            "actor": row.display_name or row.email,
            "order_number": row.order_number,
            "purchase_order_id": (
                str(row.purchase_order_id) if row.purchase_order_id else None
            ),
        }
        for row in rows
    }


async def _sold_at(
    db: AsyncSession, branch_id: uuid.UUID
) -> tuple[dict[uuid.UUID, tuple[str, bool]], dict[uuid.UUID, str]]:
    """What the branch sells, on any channel: products (name, consumes_stock)
    and their options.

    The union of three surfaces, because a product can live on any of them
    without the others — a website-only box is on no POS menu, yet the branch
    bakes and sends it, and the aggregators sell it through GrubOps:

    * the branch's live POS menu tree (`recipe_service.branch_menu_recipe_gaps`
      reads it the same way);
    * the website catalogue, when the branch takes online orders;
    * every approved GrubOps mapping, when the branch has a live GrubOps
      location (the mappings are brand-wide, one per item, not per branch).

    An option shared by several products appears once — its row is per branch,
    not per product.
    """
    from app.models.category import Category
    from app.models.external_item_map import ExternalItemMap
    from app.models.grubops import GrubOpsLocationMap
    from app.models.product import WEB_CHANNEL, sells_on
    from app.services.catalog import menu_group_service

    # A column read, not the ORM object: the tick's own Branch may carry an
    # expired attribute, and an async lazy load would raise.
    online = await db.scalar(
        select(Branch.receives_online_orders).where(Branch.id == branch_id)
    )
    product_ids: set[uuid.UUID] = set(
        (
            await db.execute(
                menu_group_service.visible_product_ids_subquery(branch_id=branch_id)
            )
        )
        .scalars()
        .all()
    )
    if online:
        product_ids.update(
            (
                await db.execute(
                    select(Product.id).where(
                        sells_on(WEB_CHANNEL),
                        or_(
                            Product.category_id.is_(None),
                            Product.category.has(Category.is_active.is_(True)),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
    mapped_options: set[uuid.UUID] = set()
    on_grubops = await db.scalar(
        select(func.count())
        .select_from(GrubOpsLocationMap)
        .where(
            GrubOpsLocationMap.branch_id == branch_id,
            GrubOpsLocationMap.is_active.is_(True),
        )
    )
    if on_grubops:
        for pid, oid in (
            await db.execute(
                select(
                    ExternalItemMap.product_id, ExternalItemMap.modifier_option_id
                ).where(
                    ExternalItemMap.system == "grubops",
                    ExternalItemMap.approved.is_(True),
                )
            )
        ).all():
            if pid is not None:
                product_ids.add(pid)
            if oid is not None:
                mapped_options.add(oid)

    products = {
        pid: (name, bool(consumes))
        for pid, name, consumes in (
            await db.execute(
                select(Product.id, Product.name, Product.consumes_stock).where(
                    Product.id.in_(product_ids) if product_ids else false(),
                    Product.is_active.is_(True),
                )
            )
        ).all()
    }
    # An option is named with the product(s) it sits on — "9 Pieces" alone
    # says nothing in an email or the audit log.
    labels: dict[uuid.UUID, tuple[str, set[str]]] = {}
    if products or mapped_options:
        for oid, name, modifier_name, product_id in (
            await db.execute(
                select(
                    ModifierOption.id,
                    ModifierOption.name,
                    Modifier.name,
                    ProductModifier.product_id,
                )
                .join(Modifier, Modifier.id == ModifierOption.modifier_id)
                .outerjoin(ProductModifier, ProductModifier.modifier_id == Modifier.id)
                .where(
                    or_(
                        ProductModifier.product_id.in_(list(products))
                        if products
                        else false(),
                        ModifierOption.id.in_(list(mapped_options))
                        if mapped_options
                        else false(),
                    ),
                    ModifierOption.is_active.is_(True),
                    Modifier.is_active.is_(True),
                )
            )
        ).all():
            _, owners = labels.setdefault(oid, (name, set()))
            owner = products.get(product_id)
            owners.add(owner[0] if owner else modifier_name)
    options: dict[uuid.UUID, str] = {
        oid: f"{' / '.join(sorted(owners))} — {name}" if owners else name
        for oid, (name, owners) in labels.items()
    }
    return products, options


async def owners_sold_at(
    db: AsyncSession, branch_id: uuid.UUID
) -> set[tuple[str, uuid.UUID]]:
    """Every recipe owner the branch sells on any channel, as `(kind, id)`:
    products that draw stock through their own recipe, and every option.

    The replenishment forecast reads this with `requirements_by_owner` for its
    availability floor — the most one sale of anything sold here draws.
    """
    products, options = await _sold_at(db, branch_id)
    return {
        (_PRODUCT, product_id)
        for product_id, (_, consumes) in products.items()
        if consumes
    } | {(_OPTION, option_id) for option_id in options}


async def _option_labels(
    db: AsyncSession, option_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """ "Product / Product — Option" for each option, as `_sold_at` names them."""
    ids = list(set(option_ids))
    if not ids:
        return {}
    labels: dict[uuid.UUID, tuple[str, set[str]]] = {}
    for oid, name, product_name in (
        await db.execute(
            select(ModifierOption.id, ModifierOption.name, Product.name)
            .join(Modifier, Modifier.id == ModifierOption.modifier_id)
            .outerjoin(ProductModifier, ProductModifier.modifier_id == Modifier.id)
            .outerjoin(Product, Product.id == ProductModifier.product_id)
            .where(ModifierOption.id.in_(ids))
        )
    ).all():
        _, owners = labels.setdefault(oid, (name, set()))
        if product_name:
            owners.add(product_name)
    return {
        oid: f"{' / '.join(sorted(owners))} — {name}" if owners else name
        for oid, (name, owners) in labels.items()
    }


def _auto_state(
    triggers: Iterable[uuid.UUID],
    names: Mapping[uuid.UUID, str],
    on_hand: Mapping[uuid.UUID, Decimal],
    now: datetime,
) -> dict:
    return {
        "items": [
            {
                "item_id": str(item_id),
                "name": names.get(item_id) or str(item_id),
                "on_hand": str(on_hand.get(item_id, Decimal(0))),
            }
            for item_id in triggers
        ],
        "at": now.isoformat(),
    }


async def _item_names(
    db: AsyncSession, item_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    ids = list(set(item_ids))
    if not ids:
        return {}
    return dict(
        (
            await db.execute(
                select(InventoryItem.id, InventoryItem.name).where(
                    InventoryItem.id.in_(ids)
                )
            )
        ).all()
    )


async def _apply(
    db: AsyncSession,
    report: BranchReport,
    branch: Branch,
    *,
    kind: str,
    owner_id: uuid.UUID,
    name: str,
    decision: Decision,
    row,
    item_names: Mapping[uuid.UUID, str],
    on_hand: Mapping[uuid.UUID, Decimal],
    now: datetime,
) -> None:
    if decision.action == "clear_override":
        await availability.clear_restock_override(db, row)
        return
    in_stock = decision.action == "on"
    writer = (
        availability.set_product_stock
        if kind == _PRODUCT
        else availability.set_option_stock
    )
    owner_arg = (
        {"product_id": owner_id} if kind == _PRODUCT else {"option_id": owner_id}
    )
    written = await writer(
        db,
        branch=branch,
        in_stock=in_stock,
        actor=availability.SYSTEM_ACTOR,
        source=availability.SOURCE_AUTO,
        reason=decision.reason,
        auto_state=(
            None
            if in_stock
            else _auto_state(decision.triggers, item_names, on_hand, now)
        ),
        entity_label=f"{name} @ {branch.reference or branch.name}",
        **owner_arg,
    )
    if written is None:
        return  # a person took the row between the read and the write
    report.changes.append(
        Change(
            owner_kind=kind,
            owner_id=owner_id,
            name=name,
            in_stock=in_stock,
            reason=decision.reason or "",
            trigger_item_ids=decision.triggers,
        )
    )


async def _email_rows(
    db: AsyncSession, branch_id: uuid.UUID, changes: list[Change]
) -> list[dict]:
    """Each change with its trigger items' current stock and latest movement."""
    item_ids = {i for change in changes for i in change.trigger_item_ids}
    names = await _item_names(db, item_ids)
    stock = await _branch_on_hand(db, branch_id, item_ids)
    movements = await _latest_movements(db, branch_id, item_ids)
    return [
        {
            "direction": "ON" if change.in_stock else "OFF",
            "kind": "Product" if change.owner_kind == _PRODUCT else "Option",
            "name": change.name,
            "reason": change.reason,
            "items": [
                {
                    "name": names.get(item_id) or str(item_id),
                    "on_hand": stock.get(item_id, Decimal(0)),
                    "movement": movements.get(item_id),
                }
                for item_id in change.trigger_item_ids
            ],
        }
        for change in changes
    ]


def _report_for(branch: Branch) -> BranchReport:
    return BranchReport(
        branch_id=branch.id,
        branch_name=branch.name,
        branch_reference=branch.reference,
    )


async def evaluate_branch(
    db: AsyncSession,
    branch: Branch,
    *,
    leaves: Mapping[tuple[str, uuid.UUID], frozenset[uuid.UUID] | None],
    item_ids: set[uuid.UUID] | None = None,
    now: datetime | None = None,
    requirements: Mapping[tuple[str, uuid.UUID], Mapping[uuid.UUID, Decimal]]
    | None = None,
) -> BranchReport:
    """Apply the decision table at one branch. Flushes; the caller commits.

    `item_ids` scopes a drain to the owners that draw those produced goods, plus
    every row at the branch that is auto-off or carries a staff override (there
    are few, and it is how a recipe that *dropped* an item releases its owner).
    None evaluates everything the branch sells — the full sweep.
    """
    now = now or _now()
    report = _report_for(branch)
    products, options = await _sold_at(db, branch.id)

    # Exception-only tables: a branch's rows are a handful. Locked so a staff
    # write racing this tick waits for it rather than being overwritten.
    product_rows = {
        row.product_id: row
        for row in (
            await db.execute(
                select(BranchProduct)
                .where(BranchProduct.branch_id == branch.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    }
    option_rows = {
        row.modifier_option_id: row
        for row in (
            await db.execute(
                select(BranchModifierOption)
                .where(BranchModifierOption.branch_id == branch.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    }

    def owner_leaves(kind: str, owner_id: uuid.UUID) -> frozenset[uuid.UUID] | None:
        if kind == _PRODUCT:
            sold = products.get(owner_id)
            # consumes_stock=False: the product's own recipe draws nothing.
            if sold is not None and not sold[1]:
                return frozenset()
        return leaves.get((kind, owner_id), frozenset())

    def tracked(row) -> bool:
        return bool(
            row.staff_override_until_restock
            or (
                row.is_in_stock is False
                and row.unavailable_source == availability.SOURCE_AUTO
            )
        )

    scope: dict[tuple[str, uuid.UUID], None] = {}
    for kind, sold_ids in ((_PRODUCT, products), (_OPTION, options)):
        for owner_id in sold_ids:
            owner = owner_leaves(kind, owner_id)
            if not owner:
                continue
            if item_ids is None or owner & item_ids:
                scope[(kind, owner_id)] = None
    for kind, rows in ((_PRODUCT, product_rows), (_OPTION, option_rows)):
        for owner_id, row in rows.items():
            if tracked(row):
                scope[(kind, owner_id)] = None

    if not scope:
        return report

    leaf_items = {
        item_id
        for kind, owner_id in scope
        for item_id in (owner_leaves(kind, owner_id) or ())
    }
    on_hand = await _branch_on_hand(db, branch.id, leaf_items)

    # Names for owners the branch no longer sells (an auto row left behind).
    missing_products = [
        oid for kind, oid in scope if kind == _PRODUCT and oid not in products
    ]
    missing_options = [
        oid for kind, oid in scope if kind == _OPTION and oid not in options
    ]
    extra_names: dict[uuid.UUID, str] = {}
    if missing_products:
        extra_names.update(
            (
                await db.execute(
                    select(Product.id, Product.name).where(
                        Product.id.in_(missing_products)
                    )
                )
            ).all()
        )
    if missing_options:
        extra_names.update(
            (
                await db.execute(
                    select(ModifierOption.id, ModifierOption.name).where(
                        ModifierOption.id.in_(missing_options)
                    )
                )
            ).all()
        )

    decisions: list[tuple[str, uuid.UUID, str, Decision, object]] = []
    for kind, owner_id in sorted(scope, key=lambda k: (k[0], str(k[1]))):
        if kind == _PRODUCT:
            sold = owner_id in products
            name = products[owner_id][0] if sold else extra_names.get(owner_id, "")
            row = product_rows.get(owner_id)
        else:
            sold = owner_id in options
            name = options[owner_id] if sold else extra_names.get(owner_id, "")
            row = option_rows.get(owner_id)
        decision = decide(
            row,
            sold=sold,
            leaves=owner_leaves(kind, owner_id),
            on_hand=on_hand,
            now=now,
            required=(requirements or {}).get((kind, owner_id)),
        )
        if decision is not None:
            decisions.append((kind, owner_id, name or str(owner_id), decision, row))

    if not decisions:
        return report
    item_names = await _item_names(
        db, {i for *_, decision, _row in decisions for i in decision.triggers}
    )
    for kind, owner_id, name, decision, row in decisions:
        await _apply(
            db,
            report,
            branch,
            kind=kind,
            owner_id=owner_id,
            name=name,
            decision=decision,
            row=row,
            item_names=item_names,
            on_hand=on_hand,
            now=now,
        )
    if report.changes:
        report.email_rows = await _email_rows(db, branch.id, report.changes)
    return report


# ─── Feature switched off ────────────────────────────────────────────────────


def _enabled_branch_ids():
    return select(BranchInventorySettings.branch_id).where(
        BranchInventorySettings.auto_availability_enabled.is_(True)
    )


async def release_disabled_branches(db: AsyncSession) -> list[BranchReport]:
    """Put back every 'auto' row at a branch whose flag is off. Flushes.

    Also clears restock overrides and dirty marks there — both only mean
    something while the branch takes part.
    """
    enabled = _enabled_branch_ids()
    reports: dict[uuid.UUID, BranchReport] = {}
    branches: dict[uuid.UUID, Branch] = {}

    async def branch_of(branch_id: uuid.UUID) -> tuple[Branch, BranchReport] | None:
        if branch_id not in branches:
            branch = await db.get(Branch, branch_id)
            if branch is None:
                return None
            branches[branch_id] = branch
            reports[branch_id] = _report_for(branch)
        return branches[branch_id], reports[branch_id]

    for model, kind, id_column, name_model in (
        (BranchProduct, _PRODUCT, BranchProduct.product_id, Product),
        (
            BranchModifierOption,
            _OPTION,
            BranchModifierOption.modifier_option_id,
            ModifierOption,
        ),
    ):
        rows = (
            await db.execute(
                select(model, name_model.name)
                .join(name_model, name_model.id == id_column)
                .where(
                    model.unavailable_source == availability.SOURCE_AUTO,
                    model.is_in_stock.is_(False),
                    model.branch_id.notin_(enabled),
                )
                .with_for_update(of=model)
            )
        ).all()
        labels = (
            await _option_labels(db, [row.modifier_option_id for row, _ in rows])
            if kind == _OPTION
            else {}
        )
        for row, name in rows:
            if kind == _OPTION:
                name = labels.get(row.modifier_option_id, name)
            found = await branch_of(row.branch_id)
            if found is None:
                continue
            branch, report = found
            owner_id = getattr(row, "product_id", None) or row.modifier_option_id
            await _apply(
                db,
                report,
                branch,
                kind=kind,
                owner_id=owner_id,
                name=name,
                decision=Decision(
                    "on",
                    availability.REASON_FEATURE_DISABLED,
                    tuple(sorted(_trigger_ids(row.auto_state), key=str)),
                ),
                row=row,
                item_names={},
                on_hand={},
                now=_now(),
            )
        await db.execute(
            update(model)
            .where(
                model.staff_override_until_restock.is_(True),
                model.branch_id.notin_(enabled),
            )
            .values(staff_override_until_restock=False)
        )
    await db.execute(delete(_dirty).where(_dirty.c.branch_id.notin_(enabled)))
    await db.flush()

    out = [report for report in reports.values() if report.changes]
    for report in out:
        report.email_rows = await _email_rows(db, report.branch_id, report.changes)
    return out


# ─── The loop ─────────────────────────────────────────────────────────────────

#: (recipe-catalogue generation, leaves) from the last load. A busy branch
#: drains after nearly every sale, and the graph only changes on an activation —
#: which bumps the generation — so a drain reuses it. The full sweep always
#: reloads, which also picks up an item whose kind was edited (no bump for that).
_leaves_cache: tuple[int, tuple[dict, dict]] | None = None


async def _leaves(db: AsyncSession, *, refresh: bool) -> tuple[dict, dict]:
    """`(leaves_by_owner, requirements_by_owner)` for the active catalogue."""
    global _leaves_cache
    generation = await recipe_service.current_catalog_generation(db)
    if not refresh and _leaves_cache is not None and _leaves_cache[0] == generation:
        return _leaves_cache[1]
    catalog = await recipe_service.load_active_catalog(db)
    maps = (leaves_by_owner(catalog), requirements_by_owner(catalog))
    _leaves_cache = (generation, maps)
    return maps


async def tick(db: AsyncSession, *, full: bool) -> list[BranchReport]:
    """One pass: release disabled branches, then drain (or fully sweep) the
    enabled ones.

    Commits the session it is handed — deliberately, and it is the loop's own
    `held_session`, never a request's: each branch is its own transaction so
    one that fails rolls back only itself, and the owner's email must go out
    only for changes that have actually committed.
    """
    reports: list[BranchReport] = []
    try:
        reports.extend(await release_disabled_branches(db))
        # Committed before the enabled branches are touched: a failure below
        # must not undo a release the owner is about to be emailed about.
        await db.commit()
    except Exception:  # noqa: BLE001 — the enabled branches still get their turn
        await db.rollback()
        reports.clear()
        logger.exception("Auto-availability: releasing disabled branches failed")

    enabled = list((await db.execute(_enabled_branch_ids())).scalars().all())
    marks: dict[uuid.UUID, dict[uuid.UUID, datetime]] = {}
    for branch_id, item_id, marked_at in (
        await db.execute(
            select(_dirty.c.branch_id, _dirty.c.item_id, _dirty.c.marked_at).where(
                _dirty.c.branch_id.in_(enabled)
            )
        )
    ).all():
        marks.setdefault(branch_id, {})[item_id] = marked_at
    work = list(enabled) if full else list(marks)
    if not work:
        # Ends the read-only transaction so the lock's connection is not left
        # idle in transaction until the next tick.
        await db.commit()
        return reports

    leaves, requirements = await _leaves(db, refresh=full)
    # Same: the catalogue is plain data from here on; release the snapshot.
    await db.commit()

    for branch_id in work:
        read = marks.get(branch_id, {})
        try:
            branch = await db.get(Branch, branch_id)
            if branch is None:
                continue
            report = await evaluate_branch(
                db,
                branch,
                leaves=leaves,
                item_ids=None if full else set(read),
                requirements=requirements,
            )
            if read:
                # Only the marks this pass read: one that landed mid-evaluation
                # carries a newer stamp and survives to the next tick.
                await db.execute(
                    delete(_dirty).where(
                        _dirty.c.branch_id == branch_id,
                        or_(
                            *(
                                and_(
                                    _dirty.c.item_id == item_id,
                                    _dirty.c.marked_at == marked_at,
                                )
                                for item_id, marked_at in read.items()
                            )
                        ),
                    )
                )
            # One branch, one transaction: its changes, audit rows and cleared
            # marks land together, and a failure rolls back only this branch.
            await db.commit()
            if report.changes:
                reports.append(report)
        except Exception:  # noqa: BLE001 — one branch must not stop the others
            await db.rollback()
            logger.exception("Auto-availability: branch %s failed", branch_id)
    return reports


async def publish(reports: list[BranchReport]) -> None:
    """After commit, off the lock's connection: tell the website, GrubOps and
    the owner. Never raises."""
    changed = [report for report in reports if report.changes]
    if not changed:
        return
    from app.services import email_service
    from app.services.catalog import catalogue_cache
    from app.services.grubops import grubops_service

    try:
        await catalogue_cache.retire()
    except Exception:  # noqa: BLE001
        logger.exception("Auto-availability: catalogue cache retire failed")

    for report in changed:
        for in_stock in (False, True):
            products = [
                c.owner_id
                for c in report.changes
                if c.in_stock is in_stock and c.owner_kind == _PRODUCT
            ]
            options = [
                c.owner_id
                for c in report.changes
                if c.in_stock is in_stock and c.owner_kind == _OPTION
            ]
            # Fire-and-forget; `grubops_reconcile` is the authority anyway.
            grubops_service.push_change_in_background(
                branch_id=report.branch_id,
                product_ids=products,
                option_ids=options,
                in_stock=in_stock,
                until=None,
            )
    # One email for the whole sweep, however many branches and items moved.
    await email_service.send_auto_availability_change(
        branches=[
            {
                "branch_name": report.branch_name,
                "branch_reference": report.branch_reference,
                "changes": report.email_rows,
            }
            for report in changed
        ]
    )


async def run_forever() -> None:
    logger.info(
        "Auto-availability sweeper started (drain every %ss, full every %ss)",
        _TICK_SECONDS,
        _FULL_SWEEP_SECONDS,
    )
    last_full: float | None = None
    while True:
        try:
            await asyncio.sleep(_TICK_SECONDS)
            await heartbeat.beat("auto_availability")
            full = last_full is None or (
                time.monotonic() - last_full >= _FULL_SWEEP_SECONDS
            )
            reports: list[BranchReport] = []
            async with advisory_lock.held_session(
                _ADVISORY_LOCK_KEY, name="auto availability"
            ) as db:
                if db is None:
                    continue
                reports = await tick(db, full=full)
                if full:
                    last_full = time.monotonic()
            # Outside the block: the lock and its connection are released before
            # the cache, GrubOps and Resend are awaited.
            await publish(reports)
        except asyncio.CancelledError:
            logger.info("Auto-availability sweeper stopping")
            raise
        except Exception:  # noqa: BLE001 — a bad tick must not kill the loop
            logger.exception("Auto-availability tick failed")
