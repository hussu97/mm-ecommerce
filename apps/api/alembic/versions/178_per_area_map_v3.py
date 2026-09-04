"""Per-area courier map v3: couriers from an averaged fresh Slider fare survey.

Same geometry as v2 (`177_per_area_map_v2` — 107 cells, unchanged), re-costed
from a fresh production Slider probe. The probe surfaced that Slider's fares
surge in the evening (~AED 3.8 a run higher than midday), so a single snapshot
would swing the whole map between Slider and Lalamove by the hour. The couriers
here are argued from the **average of two prod probes** (midday + evening,
2026-09-04) — a stable 24/7 basis — with the same rules v2 used: the AED 3
reliability margin, the Sharjah-only bike, and `third_party` above the outer fee.

Two consequences worth naming:
  * The three near-desert Dubai points (Al Awir, Lehbab, Al Marmoom) had no fare
    survey in v2 and fell to `third_party`; the fresh probe reaches them, so they
    now auto-dispatch (Al Awir slider_car, Lehbab / Al Marmoom lalamove).
  * On the averaged fares Lalamove wins more than it did on the midday-only v2
    (slider_car 36 -> 29, lalamove 9 -> 19): Slider is dearer than the first
    probe implied once the evening is weighed in.

Geometry and assignments are the committed output of `scripts.build_delivery_areas`
(`uae_delivery_zones.v6.geojson.json`, shared with v2, / `..._assignments.v3.json`);
`courier_costs.json` carries the averaged survey. Published as a new active
version; v2 is retained inactive for rollback. Carts are re-resolved.

Revision ID: 178_per_area_map_v3
Revises: 177_per_area_map_v2
Create Date: 2026-09-04
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "178_per_area_map_v3"
down_revision: Union[str, None] = "177_per_area_map_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "Per-area courier map v3"
PREVIOUS_VERSION_NAME = "Per-area courier map v2"

SHARJAH_BRANCH_REF = "K001"

DATA = Path(__file__).resolve().parents[2] / "app" / "data"
GEOMETRY_PATH = DATA / "uae_delivery_zones.v6.geojson.json"
ASSIGN_PATH = DATA / "uae_delivery_areas_assignments.v3.json"


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
            f"Per-area v3 map data missing ({GEOMETRY_PATH.name} / {ASSIGN_PATH.name}). "
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
                "Per-area map re-costed from an averaged fresh Slider probe (midday "
                "+ evening); near-desert points now auto-dispatch. Supersedes "
                + PREVIOUS_VERSION_NAME
                + "."
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
