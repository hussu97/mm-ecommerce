"""
Ensure the two aggregator-only branches exist — DSO and Karama.

Careem, Deliveroo, Talabat, Noon and Keeta sell from two kitchens that have no
storefront and no GrubOps: Dubai Silicon Oasis (DSO) and Al Karama. The
reconciliation needs a `branches` row for each so an aggregator order can resolve
a `branch_id` (and so those branches are honestly recorded as `no_maker_side` —
nothing to check against, not a discrepancy). This operator tool creates them if
they are absent and leaves them untouched if they already exist.

It is idempotent and matches **by reference**: a branch already present under the
given reference is never overwritten — the tool prints "exists" and moves on, so
running it against a live database cannot clobber an operator's edits. Only a
missing branch is created.

    DATABASE_URL=postgresql+asyncpg://... \\
        python -m scripts.seed_aggregator_branches [--dso-ref DSO] [--karama-ref KRM]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionFactory  # noqa: E402
from app.models import Branch  # noqa: E402
from app.models.branch import BranchTypeEnum  # noqa: E402

# The two aggregator-only kitchens. Reference is supplied per run (its default
# below); everything else is the sensible fixed identity of the branch.
_BRANCHES = {
    "dso": {
        "name": "Dubai Silicon Oasis",
        "city": "Dubai",
        "opening_from": "09:00",
        "opening_to": "23:00",
    },
    "karama": {
        "name": "Al Karama",
        "city": "Dubai",
        "opening_from": "09:00",
        "opening_to": "23:00",
    },
}


async def _ensure_branch(db, *, reference: str, spec: dict[str, str]) -> bool:
    """Create the branch under *reference* if absent. Returns True if created."""
    existing = await db.scalar(select(Branch).where(Branch.reference == reference))
    if existing is not None:
        print(f"  exists   {existing.name} ({existing.reference})")
        return False
    db.add(
        Branch(
            name=spec["name"],
            reference=reference,
            city=spec["city"],
            type=BranchTypeEnum.KITCHEN.value,
            timezone="Asia/Dubai",
            opening_from=spec["opening_from"],
            opening_to=spec["opening_to"],
            is_active=True,
        )
    )
    print(f"  created  {spec['name']} ({reference})")
    return True


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the DSO and Karama branches.")
    parser.add_argument("--dso-ref", default="DSO", help="reference for the DSO branch")
    parser.add_argument(
        "--karama-ref", default="KRM", help="reference for the Karama branch"
    )
    args = parser.parse_args()

    refs = {"dso": args.dso_ref, "karama": args.karama_ref}

    print("Ensuring aggregator branches:")
    created = 0
    async with AsyncSessionFactory() as db:
        for key, spec in _BRANCHES.items():
            if await _ensure_branch(db, reference=refs[key], spec=spec):
                created += 1
        # A script, not a request — it owns its transaction and commits.
        await db.commit()

    print(
        f"Done — {created} branch(es) created, {len(_BRANCHES) - created} as-is. "
        "Branch↔outlet mappings are seeded by migration 152 and editable via the "
        "admin /aggregators/branch-map API."
    )


if __name__ == "__main__":
    asyncio.run(main())
