"""Mark the noon Send / Slider-bike pair on every zone where both can deliver.

Migration `252` seeded each zone's branch priority from the `v1` fare survey,
which listed a pair-mate as an alternate **only** where the runner-up happened to
be the other of the pair (9 rows, all Slider-bike zones with noon Send behind).
The other dual-serviceable zones — noon Send won the survey and carried only
`lalamove` as an escape — never named a Slider bike, so nothing could flip them
to a bike even when a bike is live-cheaper.

This lays the refreshed `v2` survey over the top. `v2` was rebuilt from a fresh
per-branch live probe (`scripts/build_delivery_areas.py`, both branches) with one
change to the builder: the pair is made **symmetric** — a noon Send zone a Slider
bike can also reach now lists `slider_bike` as an alternate, the mirror of the
`slider_bike → noon_send` alternate the builder already produced. Across the map
that adds `slider_bike` to 32 noon-primary Sharjah/Dubai zones and nothing else:
`v1` and `v2` agree on every primary, branch and rank. That mutual listing is
exactly the signal `courier_service.comparison_candidates` reads to price the two
live per order and book the cheaper.

Guarded so it cannot fight a hand-edit, the same way `252` was. A zone is
reseeded **only while its `polygon_branch_fulfilment` rows still equal exactly
what `252` wrote from `v1`** (same branches, ranks, providers and alternates).
The moment someone edits a zone's branches in the console, this matches nothing
there and leaves it alone — including on a database restored from a dump taken
after that edit. Branches resolve by `reference` (K001 / B001); a zone whose
branch is not on this database is skipped rather than half-seeded.

The rank-1 mirror columns on `delivery_polygons` are rewritten to match, and the
active version's `revision` is bumped in the same transaction so every worker
re-reads the map (the F-COU-9 cache class).

Revision ID: 255_bike_noon_live_pair
Revises: 254_careem_gross_total
Create Date: 2026-09-18
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "255_bike_noon_live_pair"
down_revision: Union[str, None] = "254_careem_gross_total"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DATA = Path(__file__).resolve().parents[2] / "app" / "data"
V1_PATH = DATA / "uae_delivery_areas_branch_priority.v1.json"
V2_PATH = DATA / "uae_delivery_areas_branch_priority.v2.json"

_PROVIDERS = ("lalamove", "noon_send", "slider_bike", "slider_car", "third_party")


def _by_name(path: Path) -> dict[str, list[dict]]:
    return {a["name"]: a["branch_priority"] for a in json.loads(path.read_text())}


def _validate(ranks: list[dict], name: str) -> None:
    for bp in ranks:
        if bp["courier"] not in _PROVIDERS:
            raise RuntimeError(
                f"Snapshot names unknown courier {bp['courier']!r} for {name!r}"
            )
        for alt in bp.get("alternates", []):
            if alt not in _PROVIDERS:
                raise RuntimeError(
                    f"Snapshot names unknown alternate courier {alt!r} for {name!r}"
                )


def upgrade() -> None:
    if not V2_PATH.exists():
        raise RuntimeError(
            f"Refreshed branch-priority snapshot missing ({V2_PATH.name}). Run "
            "`python -m scripts.build_delivery_areas` (after a per-branch VM probe) "
            "and commit the output."
        )
    v1 = _by_name(V1_PATH)
    v2 = _by_name(V2_PATH)
    conn = op.get_bind()

    active_version = conn.execute(
        sa.text(
            "SELECT id FROM delivery_polygon_versions WHERE is_active = true LIMIT 1"
        )
    ).scalar()
    if active_version is None:
        return  # no map to seed onto

    # branch reference -> id, for every branch either snapshot names.
    refs = {
        bp["branch_ref"]
        for table in (v1, v2)
        for ranks in table.values()
        for bp in ranks
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

    def _resolved(ranks: list[dict]) -> list[tuple] | None:
        """The snapshot rows as (rank, branch_id, courier, alternates), or None if
        a branch it needs is not on this database."""
        out: list[tuple] = []
        for bp in ranks:
            ref = bp["branch_ref"]
            if ref not in branch_id_by_ref:
                return None
            out.append(
                (
                    int(bp["rank"]),
                    branch_id_by_ref[ref],
                    bp["courier"],
                    list(bp.get("alternates", [])),
                )
            )
        return out

    seeded = 0
    matched = 0
    for name, ranks_v2 in v2.items():
        ranks_v1 = v1.get(name)
        if ranks_v1 is None or ranks_v1 == ranks_v2:
            continue  # nothing to change for this zone
        _validate(ranks_v2, name)

        want_v1 = _resolved(ranks_v1)
        want_v2 = _resolved(ranks_v2)
        if want_v1 is None or want_v2 is None:
            continue  # a branch this zone needs is not on this database

        polygon_id = conn.execute(
            sa.text(
                "SELECT id FROM delivery_polygons "
                "WHERE version_id = :v AND name = :name LIMIT 1"
            ),
            {"v": active_version, "name": name},
        ).scalar()
        if polygon_id is None:
            continue
        matched += 1

        # Guard: the zone must still hold exactly what `252` wrote from v1. Read
        # the live rows in the same shape and compare; any console edit (a moved
        # rank, an added branch, a changed alternate) makes this differ and the
        # zone is left alone.
        rows = conn.execute(
            sa.text(
                "SELECT rank, branch_id, fulfilment_provider, alternate_providers "
                "FROM polygon_branch_fulfilment WHERE polygon_id = :p ORDER BY rank"
            ),
            {"p": polygon_id},
        ).fetchall()
        current = [
            (
                int(r.rank),
                r.branch_id,
                r.fulfilment_provider,
                list(r.alternate_providers),
            )
            for r in rows
        ]
        if current != want_v1:
            continue

        # Replace with the v2 list.
        conn.execute(
            sa.text("DELETE FROM polygon_branch_fulfilment WHERE polygon_id = :p"),
            {"p": polygon_id},
        )
        for rank, branch_id, courier, alts in want_v2:
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
                    "b": branch_id,
                    "rank": rank,
                    "prov": courier,
                    "alts": json.dumps(alts),
                },
            )

        # Keep the rank-1 mirror on the polygon truthful.
        top_rank, top_branch, top_courier, top_alts = want_v2[0]
        conn.execute(
            sa.text(
                "UPDATE delivery_polygons SET branch_id = :b, "
                "fulfilment_provider = :prov, "
                "alternate_providers = CAST(:alts AS jsonb) WHERE id = :p"
            ),
            {
                "p": polygon_id,
                "b": top_branch,
                "prov": top_courier,
                "alts": json.dumps(top_alts),
            },
        )
        seeded += 1

    # Name-drift guard, mirroring `252`: if branches resolved (this database has
    # the shop) yet not one changed zone was found on the active map, the snapshot
    # was built against a differently named/versioned map — fail loudly rather
    # than ship a map missing the pair.
    if branch_id_by_ref and v2 and matched == 0:
        raise RuntimeError(
            "255: refreshed branch-priority snapshot matched no changed zone on the "
            "active map by name — the map was renamed or a different version is "
            "active. Rebuild the snapshot against the live map before deploying."
        )

    if seeded:
        conn.execute(
            sa.text(
                "UPDATE delivery_polygon_versions SET revision = revision + 1 "
                "WHERE id = :v"
            ),
            {"v": active_version},
        )
    print(f"255: marked the noon/bike pair on {seeded} zone(s)")


def downgrade() -> None:
    # Irreversible in content: reverting to the exact v1 alternates per zone is
    # what a restore of the pre-255 dump does. This is a no-op so the chain can
    # still step back through it (like `252`).
    pass
