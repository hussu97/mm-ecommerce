"""
A zone's ordered branch list: who bakes it, in what order, on which courier.

A zone carries an ordered list of branches that can serve it
(`PolygonBranchFulfilment`), and the checkout walks that list in `rank` order,
giving the order to the first branch that can make the whole basket. These
routes read and rewrite that list. The three single-value columns on
`delivery_polygons` (`branch_id`, `fulfilment_provider`, `alternate_providers`)
are the rank-1 mirror of it, kept truthful here whenever the list is set.

Like the polygon and courier edit routes next door, these edit the map in
place — there is no version guard, because attributes of a zone are correctable
on the live map directly; a new map version is for a change of *geometry*. The
active map's parsed zones are cached per worker keyed by `(version id,
revision)`, so any change to the list bumps the version's `revision` in the same
transaction, exactly as an in-place fee or courier edit does, or workers keep
serving the old branch priority (the F-COU-9 cache class).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_db
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.permissions import require
from app.models.branch import Branch
from app.models.delivery_polygon import (
    DeliveryPolygon,
    DeliveryPolygonVersion,
    FulfilmentProviderEnum,
)
from app.models.polygon_branch_fulfilment import PolygonBranchFulfilment
from app.models.user import User
from app.schemas.delivery_zone import (
    BranchAssignmentResponse,
    BranchAssignmentSet,
)
from app.services import audit_service
from app.services.catalog import catalogue_cache

router = APIRouter()


async def _load_polygon(db: AsyncSession, polygon_id: uuid.UUID) -> DeliveryPolygon:
    """The zone with its version and its ordered branch list attached.

    Both are eager-loaded: the version so the audit entry can name the map and
    the revision bump can find it, and the assignments because clearing them
    through the relationship needs them in the session first.
    """
    result = await db.execute(
        select(DeliveryPolygon)
        .options(
            selectinload(DeliveryPolygon.version),
            selectinload(DeliveryPolygon.branch_fulfilments),
        )
        .where(DeliveryPolygon.id == polygon_id)
    )
    polygon = result.scalars().first()
    if polygon is None:
        raise NotFoundError("Zone not found")
    return polygon


async def _bump_revision(db: AsyncSession, version_id: uuid.UUID) -> None:
    """Move the version's cache key so every worker re-reads the branch list.

    The active map's parsed zones are cached in-process, per worker, keyed by
    `(version id, revision)`. Mutating the branch list does not change the
    version id, so without this the key never moves and only the worker that
    served the edit sees it (F-COU-9). An atomic `revision + 1` — rather than
    reading and writing back — so two edits racing on one map still land on
    distinct revisions and neither is missed. Bumped for any version, not just
    the active one, matching the in-place polygon edit exactly: a draft edited
    now and published later must not be served from a stale entry.
    """
    await db.execute(
        update(DeliveryPolygonVersion)
        .where(DeliveryPolygonVersion.id == version_id)
        .values(revision=DeliveryPolygonVersion.revision + 1)
    )


def _clean_alternates(preferred: str, alternates: list[str]) -> list[str]:
    """Validate and de-duplicate one assignment's escape list.

    Mirrors the polygon edit route: every alternate must be a known courier, the
    preferred courier cannot also be an alternate of itself, and the order the
    admin picked is preserved (`dict.fromkeys`, not `set`).
    """
    allowed = {p.value for p in FulfilmentProviderEnum}
    unknown = [c for c in alternates if c not in allowed]
    if unknown:
        raise BadRequestError(
            f"Unknown courier '{unknown[0]}'. "
            f"Choose from: {', '.join(sorted(allowed))}",
        )
    if preferred in alternates:
        raise BadRequestError(
            f"'{preferred}' already carries this branch's orders, so it cannot "
            "also be an alternate. Alternates are where an order goes when that "
            "courier will not take it.",
        )
    return list(dict.fromkeys(alternates))


def _assignments_payload(polygon: DeliveryPolygon) -> list[dict]:
    """The branch list as a plain, order-stable structure for the audit log."""
    return [
        {
            "rank": a.rank,
            "branch_id": str(a.branch_id),
            "fulfilment_provider": a.fulfilment_provider,
            "alternate_providers": list(a.alternate_providers or []),
        }
        for a in sorted(polygon.branch_fulfilments, key=lambda a: a.rank)
    ]


@router.get(
    "/polygons/{polygon_id}/assignments",
    response_model=list[BranchAssignmentResponse],
)
async def list_assignments(
    polygon_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require("delivery.manage")),
):
    """The branches that serve this zone, in rank order."""
    polygon = await _load_polygon(db, polygon_id)
    # The relationship is ordered by rank, so no sort is needed here.
    return [BranchAssignmentResponse.of(a) for a in polygon.branch_fulfilments]


@router.put(
    "/polygons/{polygon_id}/assignments",
    response_model=list[BranchAssignmentResponse],
)
async def set_assignments(
    polygon_id: uuid.UUID,
    data: BranchAssignmentSet,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Replace a zone's whole ordered branch list.

    This is the create/update/delete of assignments in one atomic replace: the
    submitted list becomes the zone's branches, rows no longer named are dropped,
    and the rank-1 branch's courier and escapes are mirrored back onto the
    polygon's single-value columns so the map and list views — which read those
    columns — stay in step with the list.

    Refuses a set whose ranks are not dense and unique from 1, or that names a
    branch twice, or an unknown courier, or a branch that does not exist: each of
    those would publish a map the checkout cannot walk unambiguously.
    """
    polygon = await _load_polygon(db, polygon_id)
    assignments = data.assignments

    if not assignments:
        raise BadRequestError(
            "A zone needs at least one branch to serve it. To clear the list "
            "entirely, delete it instead.",
        )

    # Ranks dense and unique, 1..N — so "the next branch to try" is never
    # ambiguous and never has a gap. Checked before touching the database.
    ranks = sorted(a.rank for a in assignments)
    if ranks != list(range(1, len(assignments) + 1)):
        raise BadRequestError(
            "Ranks must be 1 to N with no gaps or repeats. "
            f"Got: {sorted(a.rank for a in assignments)}.",
        )

    # Each branch appears at most once — the unique constraint enforces it too,
    # but a named error here beats a 500 from the flush.
    branch_ids = [a.branch_id for a in assignments]
    if len(set(branch_ids)) != len(branch_ids):
        raise BadRequestError(
            "A branch can appear only once in a zone's list.",
        )

    # Every preferred courier is a known one (text + CHECK, not a native enum).
    allowed = {p.value for p in FulfilmentProviderEnum}
    for a in assignments:
        if a.fulfilment_provider not in allowed:
            raise BadRequestError(
                f"Unknown courier '{a.fulfilment_provider}'. "
                f"Choose one of: {', '.join(sorted(allowed))}",
            )

    # Every branch exists and is live — present, not deleted, and active. Loaded
    # in one query rather than one per assignment. A deleted or deactivated
    # branch cannot bake, and letting one become the rank-1 mirror on the polygon
    # would point order routing at a closed kitchen, so it is refused here rather
    # than silently skipped at the checkout.
    found = (
        (await db.execute(select(Branch).where(Branch.id.in_(branch_ids))))
        .scalars()
        .all()
    )
    by_id = {b.id: b for b in found}
    for bid in branch_ids:
        branch = by_id.get(bid)
        if branch is None or branch.deleted_at is not None or not branch.is_active:
            raise BadRequestError(
                f"Branch {bid} does not exist or is not active, so nothing "
                "could bake this zone.",
            )

    before = _assignments_payload(polygon)

    # Delete-then-insert, flushed in between: the new list often reuses a rank or
    # a branch that a row being removed still holds, and the
    # `(polygon, rank)` / `(polygon, branch)` unique constraints would trip if
    # both lived in the table at once mid-transaction. `clear()` marks the old
    # rows for deletion (delete-orphan cascade); the flush issues the DELETEs
    # before the INSERTs below.
    polygon.branch_fulfilments.clear()
    await db.flush()
    for a in assignments:
        polygon.branch_fulfilments.append(
            PolygonBranchFulfilment(
                branch_id=a.branch_id,
                rank=a.rank,
                fulfilment_provider=a.fulfilment_provider,
                alternate_providers=_clean_alternates(
                    a.fulfilment_provider, a.alternate_providers
                ),
            )
        )
    await db.flush()

    # Keep the rank-1 mirror truthful. The polygon's single-value columns are the
    # preferred (rank-1) branch's, and consumers still reading the single value —
    # including the admin map and list views — read them, not the list. Letting
    # them drift from rank 1 reintroduces the "silently reverts to single-branch"
    # class of bug from the other direction.
    rank1 = min(assignments, key=lambda a: a.rank)
    polygon.branch_id = rank1.branch_id
    polygon.fulfilment_provider = rank1.fulfilment_provider
    polygon.alternate_providers = _clean_alternates(
        rank1.fulfilment_provider, rank1.alternate_providers
    )
    await db.flush()

    await audit_service.log_action(
        db,
        action="UPDATE",
        entity_type="delivery_zone_assignments",
        entity_id=str(polygon.id),
        entity_label=f"{polygon.version.name} · {polygon.name}",
        admin=admin,
        changes={"from": before, "to": _assignments_payload(polygon)},
        request=request,
    )
    # Same-transaction cache-bust: the branch priority just changed, so every
    # worker's next quote must miss its stale parse. See `_bump_revision`.
    await _bump_revision(db, polygon.version_id)
    # The storefront union is taken over the branches assigned on the active map,
    # so which branches serve — and therefore what the featured rail, add-on tray
    # and category counts list — just changed. Retire those Redis answers or they
    # serve the old set for the rest of their TTL.
    await catalogue_cache.retire()
    # Rank order, like the GET returns. The relationship's `order_by` only sorts
    # a fresh DB load; the collection we just appended to is still in the order
    # the request sent, which need not be rank order.
    return [
        BranchAssignmentResponse.of(a)
        for a in sorted(polygon.branch_fulfilments, key=lambda a: a.rank)
    ]


@router.delete(
    "/polygons/{polygon_id}/assignments",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_assignments(
    polygon_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require("delivery.manage")),
):
    """
    Remove a zone's whole branch list.

    The zone then has no assignment rows, and runtime falls back to its rank-1
    mirror columns (`branch_id` and its courier) — the single-branch behaviour a
    hand-built zone has always had. Those columns are left as they are, so the
    preferred branch keeps serving; only the alternates are gone.
    """
    polygon = await _load_polygon(db, polygon_id)
    if not polygon.branch_fulfilments:
        # Nothing to remove — and nothing to invalidate, so no revision bump.
        return

    before = _assignments_payload(polygon)
    polygon.branch_fulfilments.clear()
    await db.flush()

    await audit_service.log_action(
        db,
        action="DELETE",
        entity_type="delivery_zone_assignments",
        entity_id=str(polygon.id),
        entity_label=f"{polygon.version.name} · {polygon.name}",
        admin=admin,
        changes={"from": before, "to": []},
        request=request,
    )
    await _bump_revision(db, polygon.version_id)
    # The serving set changed (this zone falls back to its rank-1 mirror), so the
    # branch-keyed catalogue caches must be retired too — same reason as the set.
    await catalogue_cache.retire()
