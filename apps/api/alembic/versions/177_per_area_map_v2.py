"""Per-area courier map v2: finer polygons, the bike-emirate rule, AED 3 margin.

A re-simulation of the per-area map (`175_per_area_courier_map`) with three
changes, published as a new active version so rollback is one flag:

  * **The eastern desert is broken out.** The city cells of Sharjah and Dubai
    used to sprawl across the empty inland/east — Sharjah's outline reaches the
    east coast and Dubai's the desert before Hatta, and with no survey point out
    there one western cell swallowed the lot. Ten far inland/desert points now
    hold their own cells (Al Dhaid, Mleiha, Al Madam, Al Batayih; Al Awir,
    Lehbab, Al Marmoom, Al Lisaili, Margham, Al Faqa), so a suburb cell stops at
    the suburb. They carry no fare survey and fall to `third_party` on their
    outer-tier fee — the honest answer for deep-desert ground we dispatch by hand.

  * **A Slider bike only runs inside the kitchen's own emirate (Sharjah).**
    Slider's API quotes a bike fare across an emirate line up to a road ceiling,
    but a bike does not operationally cross it — so every polygon outside Sharjah
    that Slider wins is `slider_car`, and only a Sharjah area reached on a bike is
    `slider_bike`. (This moved ~22 cross-emirate polygons off the bike tier.)

  * **The reliability margin tightened from AED 5 to AED 3.** Lalamove now beats
    Slider whenever it is cheaper by more than 3 rather than 5.

Geometry and assignments are the committed output of `scripts.build_delivery_areas`
(`uae_delivery_zones.v6.geojson.json` / `uae_delivery_areas_assignments.v2.json`).
The v5 / assignments files `175` froze are left untouched, so `175` still seeds
exactly what it always did.

**Publishing is one flag.** The new polygons are new rows; activating is
`is_active = false` everywhere then `true` on this one, in this transaction. The
old per-area version is retained inactive, so rollback re-points the flag.

**Carts are re-resolved.** No cart holds a version id — only a denormalised
`delivery_quote_*` snapshot, and checkout recomputes the zone live against the
active version. So clearing the snapshot re-links every cart to this map; the
next quote rebuilds it. Orders keep their historical `polygon_id` / `zone_name`.

Revision ID: 177_per_area_map_v2
Revises: 176_remove_batching
Create Date: 2026-09-04
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "177_per_area_map_v2"
down_revision: Union[str, None] = "176_remove_batching"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "Per-area courier map v2"
PREVIOUS_VERSION_NAME = "Per-area courier map"

SHARJAH_BRANCH_REF = "K001"

DATA = Path(__file__).resolve().parents[2] / "app" / "data"
GEOMETRY_PATH = DATA / "uae_delivery_zones.v6.geojson.json"
ASSIGN_PATH = DATA / "uae_delivery_areas_assignments.v2.json"


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
            f"Per-area v2 map data missing ({GEOMETRY_PATH.name} / {ASSIGN_PATH.name}). "
            "Run `python -m scripts.build_delivery_areas` and commit the output."
        )
    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOMETRY_PATH.read_text())}
    assignments = json.loads(ASSIGN_PATH.read_text())
    missing = {a["name"] for a in assignments} - set(shapes)
    if missing:
        raise RuntimeError(f"No geometry for zones: {sorted(missing)}")

    conn = op.get_bind()

    # The kitchen every polygon is served from. Selected, not created — branches
    # are seeded by `seed_db`, not by a migration — so this is None on a
    # migration-only throwaway database and set on production. A NULL branch is a
    # valid legacy state (`delivery_polygons.branch_id` is nullable).
    branch_id = conn.execute(
        sa.text("SELECT id FROM branches WHERE reference = :ref").bindparams(
            ref=SHARJAH_BRANCH_REF
        )
    ).scalar()

    # The new version, made active in the same transaction as the step-down.
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
                "Per-area Voronoi map, re-simulated: eastern desert broken out, "
                "Slider bike restricted to the kitchen's emirate, AED 3 reliability "
                "margin. Supersedes " + PREVIOUS_VERSION_NAME + "."
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

    # Re-resolve every cart's cached estimate against the new map: clear the
    # denormalised snapshot so the next quote rebuilds it live. Guarded to carts
    # that actually hold one.
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
    # Re-point the active flag at the previous per-area map and drop this one's
    # rows.
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
