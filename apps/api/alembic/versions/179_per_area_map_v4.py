"""Per-area courier map v4: geometry rework (no split/sprawl) + finer points.

A rebuild of the map's geometry, the fault it fixes, and 35 more survey points —
the couriers keep v3's averaged Slider basis. Three changes:

  * **No polygon splits across the country, none sprawls.** A per-emirate Voronoi
    clipped to a non-contiguous emirate (Sharjah's east coast, Abu Dhabi's
    islands) or pierced by a neighbour came back as scattered fragments — one
    "zone" appearing in two places 100+ km apart (28 of the v3 cells did this) —
    or reaching hundreds of km into empty desert. The generator now keeps only
    the piece containing an area's own point, caps every cell at 40 km of reach,
    and fills the gaps locally. Empty desert beyond every cell is simply
    unserviceable, which is the honest answer, not a giant third-party polygon.
  * **Bike reachability is stricter than "same emirate".** Sharjah owns the east
    coast and a strip north of Ajman, but a bike reaching either crosses another
    emirate — so those are car, not bike, even though they are Sharjah. Bike is
    allowed only on an area in the kitchen's own contiguous Sharjah landmass.
  * **35 more points** in the dense served corridor (Sharjah, Ajman, Dubai), so
    fees and couriers are argued at neighbourhood grain (142 cells, was 107). New
    points with no fare survey fall to `third_party` on their outer-tier fee; the
    ones a fresh Slider probe reached carry their own courier.

Geometry and assignments are the committed output of `scripts.build_delivery_areas`
(`uae_delivery_zones.v7.geojson.json` / `..._assignments.v4.json`);
`courier_costs.json` carries the survey. Published as a new active version; v3 is
retained inactive for rollback. Carts are re-resolved.

Revision ID: 179_per_area_map_v4
Revises: 178_per_area_map_v3
Create Date: 2026-09-04
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "179_per_area_map_v4"
down_revision: Union[str, None] = "178_per_area_map_v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "Per-area courier map v4"
PREVIOUS_VERSION_NAME = "Per-area courier map v3"

SHARJAH_BRANCH_REF = "K001"

DATA = Path(__file__).resolve().parents[2] / "app" / "data"
GEOMETRY_PATH = DATA / "uae_delivery_zones.v7.geojson.json"
ASSIGN_PATH = DATA / "uae_delivery_areas_assignments.v4.json"


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
            f"Per-area v4 map data missing ({GEOMETRY_PATH.name} / {ASSIGN_PATH.name}). "
            "Run `python -m scripts.build_delivery_areas` and commit the output."
        )
    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOMETRY_PATH.read_text())}
    assignments = json.loads(ASSIGN_PATH.read_text())
    missing = {a["name"] for a in assignments} - set(shapes)
    if missing:
        raise RuntimeError(f"No geometry for zones: {sorted(missing)}")

    conn = op.get_bind()

    branch_id = conn.execute(
        sa.text("SELECT id FROM branches WHERE reference = :ref").bindparams(
            ref=SHARJAH_BRANCH_REF
        )
    ).scalar()

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
                "Geometry rework: single-piece, radius-capped cells (no split or "
                "sprawl); bike limited to the kitchen's Sharjah landmass; 35 more "
                "points (142 cells). Supersedes " + PREVIOUS_VERSION_NAME + "."
            ),
        },
    )

    for display_order, a in enumerate(assignments):
        geometry = shapes[a["name"]]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, delivery_fee, free_delivery_threshold, "
                " pricing_mode, free_delivery_eligible, fulfilment_provider, "
                " alternate_providers, branch_id, geometry, "
                " min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :fee, :threshold, 'static', :free, "
                " :provider, CAST(:alternates AS jsonb), :branch_id, "
                " CAST(:geometry AS jsonb), :min_lat, :max_lat, :min_lng, :max_lng, "
                " :display_order)"
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
            },
        )

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
