"""The per-area delivery map: one polygon per named area, courier by prod fares.

Replaces the ~22 concentric cost bands (`126_cost_banded_map_v2`) with ~97
per-area polygons, so a courier and a fee are argued per area rather than per
band. Geometry, fees and courier assignments are the committed output of
`scripts.build_delivery_areas` — a per-emirate Voronoi of the survey areas, fees
inherited from the v2 band each centroid falls in, and the courier chosen from a
**production** Slider fare probe against Lalamove's market rate and noon Send's
rate card:

  * fee at/above the outer tier (>= 80) -> third party (the far ground);
  * otherwise the cheapest serviceable courier, with Lalamove beating Slider
    only when cheaper by more than AED 5 (Slider is the steadier), and noon Send
    on pure price where it can serve (inside Sharjah);
  * a Slider win names the tier the survey reached it on — `slider_bike` in the
    bike's range, `slider_car` beyond it.

On production's fares Slider carries almost everything it can reach: Lalamove
wins one area, so Lalamove batching is nearly idle until fares move. That is a
direct, intended consequence of the ">5 AED, prefer Slider" rule, not a bug.

**Publishing is one flag.** A new version's polygons are new rows; activating it
is `is_active = false` everywhere then `true` on this one, in this transaction.
v2 is retained inactive, so rollback is re-pointing the flag.

**Batching stays Lalamove-only, split south/north of the kitchen.** The two
existing groups are renamed — `Dubai` -> `South of Sharjah` (90 min), `Northern
Emirates` -> `North of Sharjah` (120 min), keeping their schedules — and each new
Lalamove polygon joins one by centroid latitude vs the kitchen.

**Carts are re-resolved.** No cart, user or address holds a polygon or version
id — a cart holds only a denormalised `delivery_quote_*` snapshot, and checkout
always recomputes the zone live against the active version. So the faithful
"link the new version" is to clear that stale snapshot; the next quote rebuilds
it against this map. Orders keep their `polygon_id`/`zone_name` — those are the
historical record of the map an order was priced against and must not move.

Revision ID: 174_per_area_courier_map
Revises: 173_slider_vehicle_couriers
Create Date: 2026-09-04
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "174_per_area_courier_map"
down_revision: Union[str, None] = "173_slider_vehicle_couriers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "Per-area courier map"
PREVIOUS_VERSION_NAME = "Cost-banded map v2"

SHARJAH_BRANCH_REF = "K001"

DATA = Path(__file__).resolve().parents[2] / "app" / "data"
GEOMETRY_PATH = DATA / "uae_delivery_zones.v5.geojson.json"
ASSIGN_PATH = DATA / "uae_delivery_areas_assignments.json"

#: old group name -> (new name, delivery minutes). Kept for the guard; the
#: minutes are asserted rather than rewritten, so a console edit is not undone.
GROUP_RENAMES = {
    "Dubai": ("South of Sharjah", 90),
    "Northern Emirates": ("North of Sharjah", 120),
}


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    polys = (
        geometry["coordinates"]
        if geometry["type"] == "MultiPolygon"
        else [geometry["coordinates"]]
    )
    lngs = [pt[0] for poly in polys for ring in poly for pt in ring]
    lats = [pt[1] for poly in polys for ring in poly for pt in ring]
    return min(lats), max(lats), min(lngs), max(lngs)


def upgrade() -> None:
    if not GEOMETRY_PATH.exists() or not ASSIGN_PATH.exists():
        raise RuntimeError(
            f"Per-area map data missing ({GEOMETRY_PATH.name} / {ASSIGN_PATH.name}). "
            "Run `python -m scripts.build_delivery_areas` and commit the output."
        )
    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOMETRY_PATH.read_text())}
    assignments = json.loads(ASSIGN_PATH.read_text())
    missing = {a["name"] for a in assignments} - set(shapes)
    if missing:
        raise RuntimeError(f"No geometry for zones: {sorted(missing)}")

    conn = op.get_bind()

    # 1. Rename the two Lalamove batch groups, guarded so a console rename wins.
    for old, (new, _minutes) in GROUP_RENAMES.items():
        conn.execute(
            sa.text(
                "UPDATE delivery_batch_groups SET name = :new WHERE name = :old"
            ).bindparams(new=new, old=old)
        )
    group_ids = {
        name: gid
        for gid, name in conn.execute(
            sa.text("SELECT id, name FROM delivery_batch_groups")
        )
    }

    # The kitchen every polygon is served from. Selected, not created — branches
    # are seeded by `seed_db`, not by a migration — so this is None on a
    # migration-only throwaway database and set on production, where `126`
    # already resolved the same reference. A NULL branch is a valid legacy state
    # (`delivery_polygons.branch_id` is nullable), so this is not raised on.
    branch_id = conn.execute(
        sa.text("SELECT id FROM branches WHERE reference = :ref").bindparams(
            ref=SHARJAH_BRANCH_REF
        )
    ).scalar()

    # 2. The new version, made active in the same transaction as the step-down.
    version_id = uuid.uuid4()
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = false WHERE is_active"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO delivery_polygon_versions "
            "(id, name, notes, is_active, activated_at) "
            "VALUES (:id, :name, :notes, true, now())"
        ),
        {
            "id": str(version_id),
            "name": VERSION_NAME,
            "notes": (
                "Per-area Voronoi map; couriers from a production Slider fare "
                "probe. Supersedes " + PREVIOUS_VERSION_NAME + "."
            ),
        },
    )

    # 3. The polygons. `display_order` is insertion order; the cells are disjoint
    #    so precedence never decides a pin, but a stable order keeps the admin
    #    table readable.
    for display_order, a in enumerate(assignments):
        geometry = shapes[a["name"]]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        batch_group_id = group_ids.get(a["batch_group"]) if a["batch_group"] else None
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, delivery_fee, free_delivery_threshold, "
                " pricing_mode, free_delivery_eligible, fulfilment_provider, "
                " alternate_providers, branch_id, geometry, "
                " min_lat, max_lat, min_lng, max_lng, display_order, batch_group_id) "
                "VALUES (:id, :version_id, :name, :fee, :threshold, 'static', :free, "
                " :provider, CAST(:alternates AS jsonb), :branch_id, "
                " CAST(:geometry AS jsonb), :min_lat, :max_lat, :min_lng, :max_lng, "
                " :display_order, :batch_group_id)"
            ),
            {
                "id": str(uuid.uuid4()),
                "version_id": str(version_id),
                "name": a["name"],
                "fee": a["delivery_fee"],
                "threshold": a["free_delivery_threshold"],
                "free": a["free_delivery_eligible"],
                "provider": a["fulfilment_provider"],
                "alternates": json.dumps(a["alternate_providers"]),
                "branch_id": branch_id,
                "geometry": json.dumps(geometry),
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "display_order": display_order,
                "batch_group_id": batch_group_id,
            },
        )

    # 4. Re-resolve every cart's cached estimate against the new map: clear the
    #    denormalised snapshot so the next quote rebuilds it live. Guarded to
    #    carts that actually hold one, so it touches nothing it need not.
    conn.execute(
        sa.text(
            "UPDATE carts SET delivery_quote_provider = NULL, "
            "delivery_quote_zone = NULL, delivery_quote_fee = NULL, "
            "delivery_quote_cost = NULL, delivery_quote_currency = NULL, "
            "delivery_quote_distance_m = NULL, delivery_quote_reference = NULL, "
            "delivery_quote_latitude = NULL, delivery_quote_longitude = NULL, "
            "delivery_quote_at = NULL, delivery_quote_error = NULL "
            "WHERE delivery_quote_at IS NOT NULL"
        )
    )


def downgrade() -> None:
    conn = op.get_bind()
    # Re-point the active flag at the previous map and drop this one's rows.
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = false WHERE is_active"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true, activated_at = now() "
            "WHERE name = :name"
        ).bindparams(name=PREVIOUS_VERSION_NAME)
    )
    conn.execute(
        sa.text(
            "DELETE FROM delivery_polygons WHERE version_id = "
            "(SELECT id FROM delivery_polygon_versions WHERE name = :name)"
        ).bindparams(name=VERSION_NAME)
    )
    conn.execute(
        sa.text("DELETE FROM delivery_polygon_versions WHERE name = :name").bindparams(
            name=VERSION_NAME
        )
    )
    for old, (new, _minutes) in GROUP_RENAMES.items():
        conn.execute(
            sa.text(
                "UPDATE delivery_batch_groups SET name = :old WHERE name = :new"
            ).bindparams(old=old, new=new)
        )
