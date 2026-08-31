"""Manage the catalogue + branch hours once in MM, reconcile them to the channels.

The write-side counterpart to the ingest. Phase 1 (this module today) is the
**safe half**: build MM's desired menu/schedule, compare it against each
integrator's last read, and store the drift — no portal is written. Every write
path is gated behind `CATALOG_SYNC_ENABLED` and, even then, Phase-1 `push` runs as
a dry-run that records the plan and mutates nothing.

Layers:
- `build_mm_menu` / `build_mm_hours` — MM's catalogue and a branch's weekly
  schedule, as the channel-neutral `NormalizedMenu` / `NormalizedHours` the diff
  eats. This is the "desired" side.
- `refresh_target` — read one integrator's live menu/hours (gated by
  `CATALOG_SYNC_READ_ENABLED`) via `menu_readers`, and upsert the snapshot.
- `compute_drift` — diff the desired side against the stored snapshot and persist
  the result on the snapshot, so the admin drift report is a read.
- `refresh_all` / `compute_drift_all` — the per-target-isolated sweep (one
  target failing never blocks the rest), the same shape `ingest._sweep_all` uses.
- `plan_push` — the write entry point: 503s unless `CATALOG_SYNC_ENABLED`, and
  returns a dry-run plan (the approved deltas it *would* apply) in Phase 1.

Routing (from `docs/aggregator-catalog-hours-sync-audit.md`): menu writes for the
two integrated branches target Foodics' `Grubtech` group + price tag; non-Foodics
outlets target the portal; hours fan out per portal for every outlet.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import advisory_lock
from app.core.config import settings
from app.core.exceptions import BadRequestError, ServiceUnavailableError
from app.models.aggregator import AGGREGATOR_CHANNELS
from app.models.branch import Branch, BranchWeeklyHours
from app.models.catalog_sync import (
    SNAPSHOT_ERROR,
    SNAPSHOT_HOURS,
    SNAPSHOT_MENU,
    SNAPSHOT_OK,
    SYNC_TARGETS,
    TARGET_FOODICS,
    AggregatorMenuSnapshot,
)
from app.models.category import Category
from app.models.modifier import Modifier, ProductModifier
from app.models.product import Product
from app.services.aggregators import menu_readers
from app.services.aggregators.catalog_diff import (
    HoursDiff,
    MenuDiff,
    diff_hours,
    diff_menu,
)
from app.services.aggregators.menu_normalized import (
    NormalizedCategory,
    NormalizedHours,
    NormalizedItem,
    NormalizedMenu,
    NormalizedModifierGroup,
    NormalizedOption,
    NormalizedShift,
)
from app.services.catalog import external_item_map_service as eim

logger = logging.getLogger(__name__)

# Advisory lock, extending the aggregator series (…4805–…480A) so the catalog
# sweep never overlaps itself across the blue/green slots.
_CATALOG_SYNC_LOCK_KEY = 0x6D6D_4241_5443_480B


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── MM desired side ───────────────────────────────────────────────────────────


def _syncs_to(row: Product | Category, target: str) -> bool:
    """Whether this product/category opts in to `target` (its own switch)."""
    if not row.sync_to_aggregators:
        return False
    channels = row.sync_channels
    if channels is None:
        return True
    return target in channels


def _product_to_item(product: Product) -> NormalizedItem:
    groups: list[NormalizedModifierGroup] = []
    for pm in sorted(product.product_modifiers, key=lambda p: p.display_order):
        mod = pm.modifier
        groups.append(
            NormalizedModifierGroup(
                name=mod.name,
                external_ref=mod.reference,
                min_options=pm.minimum_options,
                max_options=pm.maximum_options,
                options=[
                    NormalizedOption(
                        name=o.name,
                        external_ref=o.sku,
                        price=Decimal(o.price) if o.price is not None else None,
                        is_available=o.is_active,
                    )
                    for o in sorted(mod.options, key=lambda x: x.display_order)
                ],
            )
        )
    return NormalizedItem(
        name=product.name,
        external_id=str(product.id),
        external_ref=product.sku,
        description=product.description,
        price=Decimal(product.base_price) if product.base_price is not None else None,
        is_available=product.is_active,
        category_ref=str(product.category_id) if product.category_id else None,
        modifier_groups=groups,
    )


async def build_mm_menu(db: AsyncSession, *, target: str) -> NormalizedMenu:
    """MM's desired menu for one target — the sync-flagged catalogue, grouped."""
    stmt = (
        select(Product)
        .where(Product.sync_to_aggregators.is_(True))
        .options(
            selectinload(Product.category),
            selectinload(Product.product_modifiers)
            .selectinload(ProductModifier.modifier)
            .selectinload(Modifier.options),
        )
        .order_by(Product.display_order, Product.name)
    )
    products = (await db.execute(stmt)).scalars().all()

    cats: dict[str, NormalizedCategory] = {}
    order: list[str] = []
    for product in products:
        if not _syncs_to(product, target):
            continue
        cat = product.category
        cat_name = cat.name if cat else "Uncategorised"
        cat_key = str(cat.id) if cat else "uncategorised"
        if cat_key not in cats:
            cats[cat_key] = NormalizedCategory(
                name=cat_name, external_id=str(cat.id) if cat else None
            )
            order.append(cat_key)
        cats[cat_key].items.append(_product_to_item(product))
    return NormalizedMenu(source="mm", categories=[cats[k] for k in order])


async def build_mm_hours(db: AsyncSession, branch_id: Any) -> NormalizedHours:
    """A branch's canonical weekly schedule as the desired hours."""
    rows = (
        (
            await db.execute(
                select(BranchWeeklyHours)
                .where(BranchWeeklyHours.branch_id == branch_id)
                .order_by(BranchWeeklyHours.weekday, BranchWeeklyHours.shift_index)
            )
        )
        .scalars()
        .all()
    )
    return NormalizedHours(
        source="mm",
        shifts=[NormalizedShift(r.weekday, r.opens, r.closes) for r in rows],
    )


_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


async def get_weekly_hours(db: AsyncSession, branch_id: Any) -> list[BranchWeeklyHours]:
    """The branch's canonical weekly shifts, ordered."""
    return list(
        (
            await db.execute(
                select(BranchWeeklyHours)
                .where(BranchWeeklyHours.branch_id == branch_id)
                .order_by(BranchWeeklyHours.weekday, BranchWeeklyHours.shift_index)
            )
        )
        .scalars()
        .all()
    )


async def set_weekly_hours(
    db: AsyncSession, branch_id: Any, shifts: list[dict[str, Any]]
) -> list[BranchWeeklyHours]:
    """Replace a branch's whole weekly schedule (a weekday with no shift = closed).

    Whole-list replace rather than per-row edits: a schedule is read and set as one
    thing, and diffing sub-rows would be its own bug surface. Validates the shape MM
    owns; each channel's writer later normalises it to that portal's limits.
    """
    cleaned: list[dict[str, Any]] = []
    per_day: dict[int, int] = {}
    for s in shifts:
        weekday = int(s["weekday"])
        opens = str(s["opens"])
        closes = str(s["closes"])
        if not (0 <= weekday <= 6):
            raise BadRequestError(f"weekday {weekday} out of range 0..6")
        if not _TIME_RE.match(opens) or not _TIME_RE.match(closes):
            raise BadRequestError(f"times must be HH:MM, got {opens}-{closes}")
        idx = per_day.get(weekday, 0)
        per_day[weekday] = idx + 1
        cleaned.append(
            {"weekday": weekday, "shift_index": idx, "opens": opens, "closes": closes}
        )

    existing = await get_weekly_hours(db, branch_id)
    for row in existing:
        await db.delete(row)
    await db.flush()
    for c in cleaned:
        db.add(BranchWeeklyHours(branch_id=branch_id, **c))
    await db.flush()
    return await get_weekly_hours(db, branch_id)


# ── Mapping reuse (external_item_map, not a parallel table) ───────────────────


async def propose_mappings_from_menu(
    db: AsyncSession, *, target: str, menu: NormalizedMenu
) -> dict[str, int]:
    """Seed the shared `external_item_map` review queue from a fetched menu.

    Reuse, not a parallel map: every category and item a read finds on `target`
    is recorded as an *unapproved* proposal (guessing the MM id by exact
    normalised-name match), so it surfaces in the one item-mappings admin queue
    alongside the ingest's own proposals. Idempotent (ON CONFLICT DO NOTHING) —
    an approved row or an operator's edit is never overwritten.
    """
    # MM name → id lookups, built once.
    prod_rows = (
        await db.execute(select(Product.id, Product.name).where(Product.is_active))
    ).all()
    cat_rows = (
        await db.execute(select(Category.id, Category.name).where(Category.is_active))
    ).all()
    prod_by_name = {eim.normalize_ref(n): pid for pid, n in prod_rows}
    cat_by_name = {eim.normalize_ref(n): cid for cid, n in cat_rows}

    counts = {"categories": 0, "items": 0}
    for cat in menu.categories:
        await eim.record_category_proposal(
            db,
            target,
            cat.name,
            guess_category_id=cat_by_name.get(eim.normalize_ref(cat.name)),
        )
        counts["categories"] += 1
        for item in cat.items:
            await eim.record_proposal(
                db,
                target,
                item.name,
                guess_product_id=prod_by_name.get(eim.normalize_ref(item.name)),
            )
            counts["items"] += 1
    await db.flush()
    return counts


# ── Hours normalisation per channel (for the writer) ──────────────────────────

#: Each portal's cap on shifts per day (audit + operations map). Keeta tops out at
#: 5 periods/day; the others take several slots. None = no known cap.
_MAX_SHIFTS_PER_DAY: dict[str, int | None] = {
    "keeta": 5,
    "careem": None,
    "talabat": None,
    "deliveroo": None,
    "noon": None,
}


def normalize_hours_for_channel(
    hours: NormalizedHours, target: str
) -> tuple[NormalizedHours, list[str]]:
    """MM's weekly schedule reshaped to one portal's limits, with warnings.

    Pure: caps shifts/day where a portal does (Keeta ≤5) and reports what it had
    to drop, so the writer never silently loses a shift. Ordering per day so the
    kept shifts are the earliest.
    """
    cap = _MAX_SHIFTS_PER_DAY.get(target)
    warnings: list[str] = []
    if cap is None:
        return hours, warnings
    by_day: dict[int, list] = {}
    for s in hours.shifts:
        by_day.setdefault(s.weekday, []).append(s)
    kept = []
    for day, shifts in by_day.items():
        shifts.sort(key=lambda x: x.opens)
        if len(shifts) > cap:
            warnings.append(
                f"{target}: weekday {day} has {len(shifts)} shifts; capped to {cap}"
            )
            shifts = shifts[:cap]
        kept.extend(shifts)
    return NormalizedHours(
        source=hours.source, shifts=kept, closures=hours.closures
    ), warnings


# ── Snapshots ─────────────────────────────────────────────────────────────────


async def _get_snapshot(
    db: AsyncSession, target: str, branch_id: Any, kind: str
) -> AggregatorMenuSnapshot | None:
    stmt = select(AggregatorMenuSnapshot).where(
        AggregatorMenuSnapshot.target == target,
        AggregatorMenuSnapshot.kind == kind,
    )
    if branch_id is None:
        stmt = stmt.where(AggregatorMenuSnapshot.branch_id.is_(None))
    else:
        stmt = stmt.where(AggregatorMenuSnapshot.branch_id == branch_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def _upsert_snapshot(
    db: AsyncSession,
    *,
    target: str,
    branch_id: Any,
    kind: str,
    source: str,
    status: str,
    raw: Any = None,
    normalized: dict | None = None,
    diff: dict | None = None,
    stats: dict | None = None,
    error: str | None = None,
) -> AggregatorMenuSnapshot:
    snap = await _get_snapshot(db, target, branch_id, kind)
    if snap is None:
        snap = AggregatorMenuSnapshot(target=target, branch_id=branch_id, kind=kind)
        db.add(snap)
    snap.source = source
    snap.status = status
    if raw is not None:
        snap.raw = raw
    if normalized is not None:
        snap.normalized = normalized
    if diff is not None:
        snap.diff = diff
    if stats is not None:
        snap.stats = stats
    if error is not None or status == SNAPSHOT_ERROR:
        snap.error = error
    if status == SNAPSHOT_OK:
        snap.fetched_at = _utcnow()
        snap.error = None
    await db.flush()
    return snap


# ── Read side (gated) ─────────────────────────────────────────────────────────


def _ensure_read_enabled() -> None:
    if not settings.CATALOG_SYNC_READ_ENABLED:
        raise ServiceUnavailableError(
            "Catalog sync reads are disabled (CATALOG_SYNC_READ_ENABLED)",
        )


async def refresh_target(
    db: AsyncSession, *, target: str, branch_id: Any, kind: str = SNAPSHOT_MENU
) -> AggregatorMenuSnapshot:
    """Read one integrator's live menu/hours and store the snapshot. Gated.

    Read-only against the portal, but it opens a marketplace session, so it is
    gated by `CATALOG_SYNC_READ_ENABLED`. A read failure marks the snapshot
    `error`/`stale` and never raises past the caller's isolation.
    """
    _ensure_read_enabled()
    try:
        if kind == SNAPSHOT_HOURS:
            hours = await menu_readers.fetch_hours(
                db, target=target, branch_id=branch_id
            )
            return await _upsert_snapshot(
                db,
                target=target,
                branch_id=branch_id,
                kind=SNAPSHOT_HOURS,
                source=menu_readers.source_for(target),
                status=SNAPSHOT_OK,
                normalized=hours.to_dict(),
            )
        menu = await menu_readers.fetch_menu(db, target=target, branch_id=branch_id)
        # Reuse: feed what we read into the shared item-map review queue (guessing
        # MM ids by exact name), so mapping review is one queue, not two.
        proposed = await propose_mappings_from_menu(db, target=target, menu=menu)
        return await _upsert_snapshot(
            db,
            target=target,
            branch_id=branch_id,
            kind=SNAPSHOT_MENU,
            source=menu_readers.source_for(target),
            status=SNAPSHOT_OK,
            normalized=menu.to_dict(),
            stats={"categories": len(menu.categories), "proposed": proposed},
        )
    except Exception as exc:  # noqa: BLE001 — record, never crash the sweep
        logger.warning("catalog-sync read failed for %s/%s: %s", target, kind, exc)
        return await _upsert_snapshot(
            db,
            target=target,
            branch_id=branch_id,
            kind=kind,
            source=menu_readers.source_for(target),
            status=SNAPSHOT_ERROR,
            error=str(exc),
        )


# ── Drift (safe, no writes) ───────────────────────────────────────────────────


async def compute_menu_drift(
    db: AsyncSession, *, target: str, branch_id: Any
) -> MenuDiff | None:
    """Diff MM's desired menu against the stored snapshot; persist + return it.

    Returns None when there is no snapshot to compare against yet (nothing has
    been read for this target/outlet) — the report shows "no data" rather than a
    misleading empty diff.
    """
    snap = await _get_snapshot(db, target, branch_id, SNAPSHOT_MENU)
    if snap is None or snap.normalized is None:
        return None
    desired = await build_mm_menu(db, target=target)
    actual = NormalizedMenu.from_dict(snap.normalized)
    result = diff_menu(
        desired,
        actual,
        target=target,
        enforce_price_parity=settings.CATALOG_SYNC_ENFORCE_PRICE_PARITY,
    )
    snap.diff = result.to_dict()
    await db.flush()
    return result


async def compute_hours_drift(
    db: AsyncSession, *, target: str, branch_id: Any
) -> HoursDiff | None:
    snap = await _get_snapshot(db, target, branch_id, SNAPSHOT_HOURS)
    if snap is None or snap.normalized is None:
        return None
    desired = await build_mm_hours(db, branch_id)
    actual = NormalizedHours.from_dict(snap.normalized)
    result = diff_hours(desired, actual, target=target)
    snap.diff = result.to_dict()
    await db.flush()
    return result


# ── Write side (hard-gated, dry-run in Phase 1) ───────────────────────────────


def _ensure_write_enabled() -> None:
    if not settings.CATALOG_SYNC_ENABLED:
        raise ServiceUnavailableError(
            "Catalog sync writes are disabled (CATALOG_SYNC_ENABLED)",
        )


async def plan_push(
    db: AsyncSession, *, target: str, branch_id: Any, kind: str = SNAPSHOT_MENU
) -> dict[str, Any]:
    """The write entry point. 503s unless enabled; a dry-run plan even then.

    Phase 1 never mutates a portal: with the flag on it returns the plan (the
    deltas it *would* apply, routed per the audit's rule) so the shape can be
    reviewed; the actual writers land in a later phase behind this same flag.
    """
    _ensure_write_enabled()
    warnings: list[str] = []
    if kind == SNAPSHOT_HOURS:
        drift = await compute_hours_drift(db, target=target, branch_id=branch_id)
        deltas = drift.to_dict()["deltas"] if drift else []
        # The concrete thing the writer would set: MM's schedule, normalised to
        # this portal's limits (Keeta ≤5 periods/day).
        desired = await build_mm_hours(db, branch_id)
        shaped, warnings = normalize_hours_for_channel(desired, target)
        ops = [
            {
                "op": "set_weekly_schedule",
                "shifts": [s.to_dict() for s in shaped.shifts],
            }
        ]
    else:
        drift = await compute_menu_drift(db, target=target, branch_id=branch_id)
        deltas = drift.to_dict()["deltas"] if drift else []
        snap = await _get_snapshot(db, target, branch_id, SNAPSHOT_MENU)
        actual = (
            NormalizedMenu.from_dict(snap.normalized)
            if snap and snap.normalized
            else NormalizedMenu(source=target)
        )
        ops = _build_menu_ops(deltas, actual)
    return {
        "dry_run": True,
        "target": target,
        "route": _route_for(target),
        "kind": kind,
        "would_apply": deltas,
        "operations": ops,
        "warnings": warnings,
        "note": (
            "Phase-1 push is a dry run — no portal, Foodics group/price tag, or "
            "hours were modified. `operations` is the concrete plan the writer "
            "would execute (menu ops carry the channel id resolved from the last "
            "read; the Foodics route targets the Grubtech group + price tag)."
        ),
    }


def _build_menu_ops(
    deltas: list[dict[str, Any]], actual: NormalizedMenu
) -> list[dict[str, Any]]:
    """Turn diff deltas into concrete write ops, resolving each channel id off the
    last read (the snapshot's normalised menu) — pure, so it is unit-testable and
    the writer just executes it."""
    # name → channel external_id, from what we actually read.
    ext_by_name: dict[str, str | None] = {}
    for cat in actual.categories:
        for item in cat.items:
            ext_by_name[_norm(item.name)] = item.external_id
    ops: list[dict[str, Any]] = []
    for d in deltas:
        op = {
            "action": d["action"],
            "kind": d["kind"],
            "entity": d["entity"],
            "channel_external_id": ext_by_name.get(_norm(d["entity"])),
            "mm_value": d.get("mm_value"),
            "channel_value": d.get("channel_value"),
        }
        ops.append(op)
    return ops


def _norm(name: str | None) -> str:
    return " ".join((name or "").strip().lower().replace("&", " and ").split())


def _route_for(target: str) -> str:
    """Where a write for this target lands (audit §2c)."""
    if target == TARGET_FOODICS:
        return "foodics_grubtech_group_and_price_tag"
    return "channel_portal"


# ── Sweeps (per-target isolation, the ingest's pattern) ───────────────────────


def _resolve_targets(targets: list[str] | None) -> list[str]:
    if not targets:
        return list(SYNC_TARGETS)
    bad = [t for t in targets if t not in SYNC_TARGETS]
    if bad:
        raise ServiceUnavailableError(f"Unknown sync target(s): {', '.join(bad)}")
    return targets


async def compute_drift_all(
    db: AsyncSession, *, branch_id: Any, targets: list[str] | None = None
) -> dict[str, Any]:
    """Recompute menu + hours drift for every target of one branch, isolated."""
    out: dict[str, Any] = {}
    for target in _resolve_targets(targets):
        try:
            menu = await compute_menu_drift(db, target=target, branch_id=branch_id)
            hours = await compute_hours_drift(db, target=target, branch_id=branch_id)
            out[target] = {
                "menu": menu.to_dict() if menu else None,
                "hours": hours.to_dict() if hours else None,
            }
        except Exception as exc:  # noqa: BLE001 — one target must not stop the rest
            await db.rollback()
            logger.warning("catalog-sync drift failed for %s: %s", target, exc)
            out[target] = {"error": str(exc)}
    return out


async def refresh_all(
    db: AsyncSession,
    *,
    branch_id: Any,
    targets: list[str] | None = None,
    kinds: tuple[str, ...] = (SNAPSHOT_MENU, SNAPSHOT_HOURS),
) -> dict[str, Any]:
    """Read every target's menu/hours for one branch. Gated + per-target isolated.

    Foodics is read account-level (branch None); the marketplaces per outlet.
    Holds the catalog advisory lock so two slots don't sweep at once.
    """
    _ensure_read_enabled()
    out: dict[str, Any] = {}
    async with advisory_lock.held(
        _CATALOG_SYNC_LOCK_KEY, name="catalog sync refresh"
    ) as mine:
        if not mine:
            return {"skipped": "another catalog-sync refresh is already running"}
        for target in _resolve_targets(targets):
            target_branch = None if target == TARGET_FOODICS else branch_id
            results: dict[str, str] = {}
            for kind in kinds:
                if target == TARGET_FOODICS and kind == SNAPSHOT_HOURS:
                    continue  # Foodics never carries aggregator hours
                try:
                    snap = await refresh_target(
                        db, target=target, branch_id=target_branch, kind=kind
                    )
                    results[kind] = snap.status
                    # Commit per (target, kind), like the ingest sweep commits per
                    # channel: a snapshot that read cleanly must persist even if a
                    # later target's read then throws and rolls its own work back.
                    # Waiting for the request to commit would lose every earlier
                    # target when one fails — the opposite of the isolation the
                    # try/except gives. This is a sweep, not a single request.
                    await db.commit()
                except Exception as exc:  # noqa: BLE001
                    await db.rollback()
                    logger.warning("catalog-sync refresh %s/%s: %s", target, kind, exc)
                    results[kind] = f"error: {exc}"
            out[target] = results
    return out


async def integrated_branches(db: AsyncSession) -> list[Branch]:
    """The Foodics-integrated branches (Sharjah, Barsha) — menu routes to Foodics."""
    branches = (
        (await db.execute(select(Branch).where(Branch.is_active.is_(True))))
        .scalars()
        .all()
    )
    return [b for b in branches if b.has_foodics]


__all__ = [
    "build_mm_menu",
    "build_mm_hours",
    "refresh_target",
    "refresh_all",
    "compute_menu_drift",
    "compute_hours_drift",
    "compute_drift_all",
    "plan_push",
    "integrated_branches",
    "AGGREGATOR_CHANNELS",
]
