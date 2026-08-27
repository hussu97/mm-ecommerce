"""Tying a marketplace's outlets to this shop's branches — the mapping rows.

Capability is a row (see `app.models.aggregator`): a branch is *on* Careem iff
it has an active `aggregator_branch_map` row, *has* Foodics iff it has a
`foodics_branch_map` row. This module is where those rows are written — the
provider tells us which outlets an account owns and what area each sits in, and
this layer resolves each area to one of our branches and upserts the mapping.

Area→branch matching is a small, configurable table rather than hardcoded ids:
the account's own outlet numbers change when an outlet is re-onboarded, but the
*area* a Careem outlet trades in is stable and human-legible ("Barsha Heights",
"Silicon Oasis", "Al Majaz"). Each area maps to a branch *hint* — a substring
matched case-insensitively against the branch's name, city and reference — so
"Silicon Oasis" finds the DSO branch whatever its reference happens to be, and
"Al Majaz" finds Sharjah. An outlet whose area is unknown, or whose hint matches
no branch, is logged and skipped rather than mapped to the wrong branch.

Per the transaction convention, nothing here commits: the upserts `flush`, and
the caller's `get_db` (or an operator script) commits.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.aggregator import (
    CHANNEL_CAREEM,
    AggregatorBranchMap,
    FoodicsBranchMap,
)
from app.models.base import utcnow
from app.models.branch import Branch
from app.services.aggregators.session_store import LoadedSession
from app.services.providers import careem_provider

logger = logging.getLogger(__name__)

#: Careem area name (lower-cased) → the branch hint to resolve it to. The hint
#: is matched against a branch's name, city and reference, so it survives an
#: outlet being re-onboarded under a new id and does not depend on a branch's
#: reference string. Extend this when a new area is onboarded — it is a data
#: edit, not a code change, and callers may pass their own map.
DEFAULT_AREA_TO_BRANCH_HINT: dict[str, str] = {
    "barsha heights": "barsha",
    "silicon oasis": "silicon oasis",
    "al majaz": "sharjah",
}

#: The initial outlet↔branch mapping is DB data, loaded by migration
#: `152_aggregator_branch_map_seed` and thereafter editable through the admin
#: API (`/aggregators/branch-map`) — deliberately NOT a constant here, so a
#: re-onboarded outlet or a new branch is a row edit, not a code deploy. This
#: module keeps only the *behaviour*: the upsert, the Foodics helper, and the
#: live Careem discovery that writes the same rows from the portal's own list.


async def _resolve_branch(db: AsyncSession, hint: str):
    """The first active branch whose name, city or reference contains *hint*."""
    like = f"%{hint.lower()}%"
    return await db.scalar(
        select(Branch.id)
        .where(
            or_(
                func.lower(Branch.name).like(like),
                func.lower(func.coalesce(Branch.city, "")).like(like),
                func.lower(Branch.reference).like(like),
            )
        )
        .order_by(Branch.display_order, Branch.reference)
        .limit(1)
    )


async def upsert_branch_map(
    db: AsyncSession,
    *,
    channel: str,
    branch_id,
    external_outlet_id: str | None = None,
    external_brand_id: str | None = None,
    external_company_id: str | None = None,
    channel_ref: str | None = None,
    is_active: bool = True,
) -> None:
    """Upsert one `(channel, branch)` mapping row, keyed on the unique pair.

    Idempotent: a re-run over the same outlet refreshes the external ids and the
    active flag rather than inserting a duplicate.
    """
    values = {
        "channel": channel,
        "branch_id": branch_id,
        "external_outlet_id": external_outlet_id,
        "external_brand_id": external_brand_id,
        "external_company_id": external_company_id,
        "channel_ref": channel_ref,
        "is_active": is_active,
    }
    update = {k: v for k, v in values.items() if k not in ("channel", "branch_id")}
    update["updated_at"] = utcnow()
    await db.execute(
        pg_insert(AggregatorBranchMap)
        .values(**values)
        .on_conflict_do_update(constraint="uq_aggregator_branch_map", set_=update)
    )
    await db.flush()


async def ensure_foodics_map(
    db: AsyncSession, branch_id, foodics_branch_id: str, *, is_active: bool = True
) -> None:
    """Upsert the Foodics mapping for one branch, keyed on `branch_id`.

    Mirrors `grubops_location_map` so "has Foodics" is a row; idempotent on the
    branch's unique `branch_id`.
    """
    values = {
        "branch_id": branch_id,
        "foodics_branch_id": foodics_branch_id,
        "is_active": is_active,
    }
    await db.execute(
        pg_insert(FoodicsBranchMap)
        .values(**values)
        .on_conflict_do_update(
            index_elements=["branch_id"],
            set_={
                "foodics_branch_id": foodics_branch_id,
                "is_active": is_active,
                "updated_at": utcnow(),
            },
        )
    )
    await db.flush()


async def map_careem(
    db: AsyncSession,
    session: LoadedSession,
    *,
    area_map: dict[str, str] | None = None,
) -> int:
    """Discover Careem's outlets and upsert a branch-map row for each.

    Each outlet's `area_name` is resolved to a branch through *area_map* (the
    default table above unless one is passed); the row carries the outlet /
    brand / company ids and mirrors Careem's own `active` flag. Returns the
    number of outlets mapped; unknown-area or unmatched outlets are skipped with
    a log line rather than mapped wrongly.
    """
    area_map = area_map or DEFAULT_AREA_TO_BRANCH_HINT
    outlets = await careem_provider.provider.discover_outlets(session)
    mapped = 0
    for outlet in outlets:
        outlet_id = outlet.get("external_outlet_id")
        area_name = (outlet.get("area_name") or "").strip()
        hint = area_map.get(area_name.lower())
        if not hint:
            logger.warning(
                "careem outlet %s: area %r has no branch mapping — skipped",
                outlet_id,
                area_name,
            )
            continue
        branch_id = await _resolve_branch(db, hint)
        if branch_id is None:
            logger.warning(
                "careem outlet %s: area %r → hint %r matched no branch — skipped",
                outlet_id,
                area_name,
                hint,
            )
            continue
        await upsert_branch_map(
            db,
            channel=CHANNEL_CAREEM,
            branch_id=branch_id,
            external_outlet_id=outlet_id,
            external_brand_id=outlet.get("external_brand_id"),
            external_company_id=outlet.get("external_company_id"),
            is_active=bool(outlet.get("active")),
        )
        mapped += 1
    return mapped


async def outlet_ids_for_channel(db: AsyncSession, channel: str) -> list[str]:
    """Active `external_outlet_id` values for a channel — the store ids exports scope to."""
    rows = await db.scalars(
        select(AggregatorBranchMap.external_outlet_id).where(
            AggregatorBranchMap.channel == channel,
            AggregatorBranchMap.is_active.is_(True),
            AggregatorBranchMap.external_outlet_id.is_not(None),
        )
    )
    return [str(outlet_id) for outlet_id in rows if outlet_id]


async def finance_accounts_for_channel(
    db: AsyncSession, channel: str
) -> list[dict[str, str]]:
    """Finance API account descriptors from the branch map.

    Each active row becomes `{grid, billingParentId, chainId}` — the shape
    Talabat's finance gateway expects. Other channels can reuse the same helper
    when their portal keys payouts on outlet + brand ids stored in the map.
    """
    rows = await db.scalars(
        select(AggregatorBranchMap).where(
            AggregatorBranchMap.channel == channel,
            AggregatorBranchMap.is_active.is_(True),
            AggregatorBranchMap.external_outlet_id.is_not(None),
        )
    )
    accounts: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        grid = str(row.external_outlet_id or "").strip()
        if not grid:
            continue
        brand = str(row.external_brand_id or grid).strip() or grid
        key = (grid, brand)
        if key in seen:
            continue
        seen.add(key)
        accounts.append(
            {
                "grid": grid,
                "billingParentId": brand,
                "chainId": brand,
            }
        )
    return accounts
