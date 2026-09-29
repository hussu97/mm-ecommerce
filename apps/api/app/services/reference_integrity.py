"""
Referential integrity for the columns the database cannot enforce it on.

Six columns across two tables store a `UUID[]` instead of a join table:
`devices.category_ids`, and `promotions.branch_ids` / `auto_branch_ids` /
`coupon_branch_ids` / `trigger_product_ids` / `reward_product_ids`. (Three more
tables — `discounts`, `timed_events`, `notification_rules` — carried the same
shape until they were dropped, never used, in `307_drop_dead_tables`.)

Postgres has no way to say "every element of this array is a live `branches.id`".
So nothing does. A branch can be deleted while three promotions still scope
themselves to it; a typo in an id is accepted and simply never matches. None
of it errors — the
scoping just silently stops meaning what it says, which is the worst failure
mode a discount rule can have, because the symptom is *money* and the cause is
invisible.

The proper fix is join tables, and the audit says so — but that is a migration
plus an admin UI change per column. This is the cheap half that closes the hole
that actually bites: **an id is checked at the moment somebody writes it.** A
row that gets past this check names things that existed and were live when it
was saved. A branch deleted afterwards is a different (and rarer) problem, and
one a reader can see, because the scoping list still names something real.

Declared here rather than on the models so there is one list to read, and
consulted from `crud_service.create`/`update` rather than from each router,
because that is the one door all of these writes already go through — the
promotion routes are built by `pos_config.build_crud_router` and never touch a
hand-written handler at all.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.exceptions import BadRequestError

__all__ = ["check"]


#: `{model name: {array column: referenced model name}}`.
#:
#: Names rather than classes, resolved lazily against `app.models`, so this
#: module stays importable from `crud_service` without dragging every mapped
#: class into its import graph.
REFERENCES: dict[str, dict[str, str]] = {
    # Which menu categories this terminal shows. A display routed to a category
    # that no longer exists shows an empty screen and says nothing about why.
    "Device": {"category_ids": "Category"},
    "Promotion": {
        # Empty means every branch, so a *wrong* id is not "no branches" — it
        # is "this branch", for a branch that is not there. The promotion then
        # applies nowhere while the console shows it scoped to one site.
        "branch_ids": "Branch",
        # Where it runs automatically / as a till coupon. A stale id is a mode
        # set for a branch that is not there.
        "auto_branch_ids": "Branch",
        "coupon_branch_ids": "Branch",
        # The two that decide money: what triggers the promotion and what it
        # gives away. A stale id here is a promotion that quietly stops firing,
        # or one whose free product cannot be granted.
        "trigger_product_ids": "Product",
        "reward_product_ids": "Product",
    },
}


def _target(name: str) -> Any:
    """The mapped class for *name*, imported at call time."""
    import app.models as models

    target = getattr(models, name, None)
    if target is None:  # pragma: no cover - a typo in REFERENCES, not a runtime path
        raise RuntimeError(f"reference_integrity names an unknown model: {name}")
    return target


async def _live_ids(db, target: Any, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """
    Which of *ids* name a row that exists and is currently usable.

    "Usable" is whatever the target models: soft-deleted rows are excluded where
    the table has `deleted_at`, deactivated ones where it has `is_active`. Both
    matter for the same reason — a promotion scoped to a switched-off branch is
    a promotion that does nothing, and the person saving it believes otherwise.

    Checked at write time only. An entity deactivated *later* does not
    retroactively invalidate a row that already names it; that would turn
    switching a product off into a wall of failing saves on unrelated screens.
    """
    stmt = select(target.id).where(target.id.in_(ids))
    if hasattr(target, "deleted_at"):
        stmt = stmt.where(target.deleted_at.is_(None))
    if hasattr(target, "is_active"):
        stmt = stmt.where(target.is_active.is_(True))
    return set((await db.execute(stmt)).scalars().all())


async def check(db, model: type, payload: dict[str, Any]) -> None:
    """
    Refuse a write whose UUID arrays point at things that are not there.

    A no-op for every model not in `REFERENCES`, and for every field the payload
    does not set — `crud_service.update` dumps with `exclude_unset`, so an edit
    that does not touch the scoping is not made to re-prove it.

    `BadRequestError` rather than `ConflictError`: the caller sent an id that
    names nothing, which is a bad request in the plainest sense. The message
    lists the offending ids, because "one of these fourteen branch ids is wrong"
    is not a message anybody can act on.
    """
    spec = REFERENCES.get(model.__name__)
    if not spec:
        return

    for field, target_name in spec.items():
        raw = payload.get(field)
        if not raw:
            # Absent, None or empty. Empty is meaningful for every one of these
            # columns — it means "all of them" — and has nothing to verify.
            continue

        ids = list(dict.fromkeys(raw))
        live = await _live_ids(db, _target(target_name), ids)
        missing = [str(value) for value in ids if value not in live]
        if missing:
            raise BadRequestError(
                f"{field} names {target_name.lower()}s that do not exist or are "
                f"no longer active: {', '.join(missing)}"
            )
