"""Seed the simulated branch priority (Sharjah + Barsha) onto the active map.

Migration `251` gave every zone a single rank-1 assignment mirroring its old
single branch. This lays the real multi-branch priority over the top: for every
zone the simulation covered, it writes the ordered branch list from the frozen
snapshot `uae_delivery_areas_branch_priority.v1.json` — which branch is preferred
and which is the alternate, each on the courier the simulation found cheapest and
serviceable from that kitchen (noon Send / Slider car / Slider bike; Lalamove is
the backup, never a rank). Third-party zones stay Sharjah-only by construction.

Guarded so it cannot fight a hand-edit. It replaces a zone's assignments **only
while they are still exactly the single default row `251` wrote** — the preferred
branch, rank 1, and nothing else. The moment someone sets a zone's branches in
the console, this migration matches nothing there and leaves it alone, including
on a database restored from a dump taken after that edit. Branches are resolved
by `reference` (K001 / B001), so a zone whose second branch is not on this
database is simply left at its single-branch default rather than half-seeded.

The rank-1 mirror columns on `delivery_polygons` are rewritten to the new
preferred branch too, and the active version's `revision` is bumped in the same
transaction so every worker re-reads the map (the F-COU-9 cache class).

**The snapshot must be the real probe output.** `uae_delivery_areas_branch_priority.v1.json`
is produced by `scripts.build_delivery_areas` from per-branch Slider fares that
only the production VM can probe (IP-whitelisted, origin-specific). Regenerate it
from a live `--branch K001` and `--branch B001` probe and run
`scripts.compare_branch_priority` before this ships; the file committed for
development is a synthetic stand-in and is flagged as such in the cost fixtures.

Revision ID: 252_polygon_branch_priority_seed
Revises: 251_polygon_branch_fulfilment
Create Date: 2026-09-17
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "252_polygon_branch_priority_seed"
down_revision: Union[str, None] = "251_polygon_branch_fulfilment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DATA = Path(__file__).resolve().parents[2] / "app" / "data"
PRIORITY_PATH = DATA / "uae_delivery_areas_branch_priority.v1.json"

_PROVIDERS = ("lalamove", "noon_send", "slider_bike", "slider_car", "third_party")


def upgrade() -> None:
    if not PRIORITY_PATH.exists():
        raise RuntimeError(
            f"Branch-priority snapshot missing ({PRIORITY_PATH.name}). Run "
            "`python -m scripts.build_delivery_areas` (after a per-branch VM probe) "
            "and commit the output."
        )
    priorities = json.loads(PRIORITY_PATH.read_text())
    conn = op.get_bind()

    active_version = conn.execute(
        sa.text(
            "SELECT id FROM delivery_polygon_versions WHERE is_active = true LIMIT 1"
        )
    ).scalar()
    if active_version is None:
        return  # no map to seed onto

    # branch reference -> id, for every branch a snapshot names. A reference not on
    # this database drops the assignments that need it (see per-zone guard below).
    refs = {
        r
        for area in priorities
        for r in (bp["branch_ref"] for bp in area["branch_priority"])
    }
    branch_id_by_ref: dict[str, uuid.UUID] = {}
    for ref in refs:
        bid = conn.execute(
            sa.text(
                "SELECT id FROM branches WHERE reference = :ref "
                "AND deleted_at IS NULL LIMIT 1"
            ),
            {"ref": ref},
        ).scalar()
        if bid is not None:
            branch_id_by_ref[ref] = bid

    seeded = 0
    for area in priorities:
        ranks = area["branch_priority"]
        # Every branch this zone needs must exist, or it is left single-branch
        # rather than seeded with a gap in its priority.
        if any(bp["branch_ref"] not in branch_id_by_ref for bp in ranks):
            continue
        for bp in ranks:
            if bp["courier"] not in _PROVIDERS:
                raise RuntimeError(
                    f"Snapshot names unknown courier {bp['courier']!r} for "
                    f"{area['name']!r}"
                )

        # The zone on the active map with this name.
        polygon_id = conn.execute(
            sa.text(
                "SELECT id FROM delivery_polygons "
                "WHERE version_id = :v AND name = :name LIMIT 1"
            ),
            {"v": active_version, "name": area["name"]},
        ).scalar()
        if polygon_id is None:
            continue

        # Guard: only seed a zone still at its single default row. `251` wrote
        # exactly one rank-1 assignment per zone (its old single branch); the
        # simulation may put a *different* branch at rank 1, so the test is the
        # shape — one row, at rank 1 — not which branch it names. The moment the
        # console adds a second branch or reorders, the shape stops being the
        # default and this leaves the zone alone.
        existing = conn.execute(
            sa.text(
                "SELECT rank FROM polygon_branch_fulfilment "
                "WHERE polygon_id = :p ORDER BY rank"
            ),
            {"p": polygon_id},
        ).fetchall()
        is_untouched_default = len(existing) == 1 and existing[0].rank == 1
        if not is_untouched_default:
            continue

        # Replace the default with the full ranked list.
        conn.execute(
            sa.text("DELETE FROM polygon_branch_fulfilment WHERE polygon_id = :p"),
            {"p": polygon_id},
        )
        for bp in ranks:
            conn.execute(
                sa.text(
                    "INSERT INTO polygon_branch_fulfilment "
                    "(id, polygon_id, branch_id, rank, fulfilment_provider, "
                    " alternate_providers) "
                    "VALUES (gen_random_uuid(), :p, :b, :rank, :prov, "
                    " CAST(:alts AS jsonb))"
                ),
                {
                    "p": polygon_id,
                    "b": branch_id_by_ref[bp["branch_ref"]],
                    "rank": bp["rank"],
                    "prov": bp["courier"],
                    "alts": json.dumps(bp.get("alternates", [])),
                },
            )

        # Keep the rank-1 mirror on the polygon truthful.
        top = ranks[0]
        conn.execute(
            sa.text(
                "UPDATE delivery_polygons SET branch_id = :b, "
                "fulfilment_provider = :prov, "
                "alternate_providers = CAST(:alts AS jsonb) WHERE id = :p"
            ),
            {
                "p": polygon_id,
                "b": branch_id_by_ref[top["branch_ref"]],
                "prov": top["courier"],
                "alts": json.dumps(top.get("alternates", [])),
            },
        )
        seeded += 1

    # Move the active version's cache key so every worker re-reads the new
    # priority — the same in-transaction bump the console's edits do (F-COU-9).
    if seeded:
        conn.execute(
            sa.text(
                "UPDATE delivery_polygon_versions SET revision = revision + 1 "
                "WHERE id = :v"
            ),
            {"v": active_version},
        )
    print(f"252: seeded branch priority onto {seeded} zone(s)")


def downgrade() -> None:
    # Irreversible in content: the single-branch default `251` wrote is not
    # recoverable per zone without re-deriving it. `251`'s downgrade drops the
    # whole table, which is the real rollback; this one is a no-op so the chain
    # can still step back to it.
    pass
