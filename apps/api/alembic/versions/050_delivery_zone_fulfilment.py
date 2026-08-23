"""Price each zone for the courier that actually serves it.

Version 048 drew the map as seven emirates carrying the old flat fees. Two
things were wrong with that once a real courier was priced against it.

First, an emirate is not a delivery zone. Sharjah reaches Khor Fakkan, Dubai
reaches Hatta, Ajman owns two inland exclaves — places that cost three to six
times what the city next door costs. Charging them the city fee loses money on
every order.

Second, nothing recorded *who* delivers. That decision is per zone and it now
has consequences in code: a Lalamove zone books itself, a third-party zone
stays the manual flow it is today.

So `delivery_polygons` gains `fulfilment_provider`, and a new map is published:

  Sharjah City (25 km)  15 AED  lalamove
  Ajman City   (30 km)  15 AED  lalamove
  Dubai City   (40 km)  25 AED  lalamove
  everything else       50 AED  third_party

The radii are the reach the live rate card was measured over, and each city is
listed ahead of its own emirate so the smaller shape wins the lookup. The
remainder of every emirate, and anything outside all of them, stays at 50 AED —
we still deliver there, just not on Lalamove. The free-delivery threshold is
untouched and applies in every zone.

Fees come from the Lalamove UAE feasibility study (lalamove-poc,
`Lalamove_Melting_Moments.xlsx`, sheet 2): AED 15 clears cost in Sharjah and
Ajman, AED 25 clears it across Dubai, and no fee a customer would pay clears a
130 AED Abu Dhabi run.

048's map is left in place and merely deactivated, so rolling back is pointing
`is_active` at it again.

Numbered 050 rather than 049 because the home-page work took 049 off the same
parent while this was in flight. Both chains hung off 048 until they met; this
one re-parented onto 049 at the merge, which is why the numbers skip nothing
but the history has a fork in it.

Revision ID: 050_delivery_zone_fulfilment
Revises: 049_home_page_sections
Create Date: 2026-08-02
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "050_delivery_zone_fulfilment"
down_revision: Union[str, None] = "049_home_page_sections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VERSION_NAME = "Lalamove strategy v1"

# zone name -> (fee, region slug, fulfilment provider)
ZONES: dict[str, tuple[str, str, str]] = {
    "Sharjah City": ("15.00", "sharjah", "lalamove"),
    "Ajman City": ("15.00", "ajman", "lalamove"),
    "Dubai City": ("25.00", "dubai", "lalamove"),
    "Sharjah": ("50.00", "sharjah", "third_party"),
    "Ajman": ("50.00", "ajman", "third_party"),
    "Dubai": ("50.00", "dubai", "third_party"),
    "Abu Dhabi": ("50.00", "abu_dhabi", "third_party"),
    "Ras al-Khaimah": ("50.00", "ras_al_khaimah", "third_party"),
    "Fujairah": ("50.00", "fujairah", "third_party"),
    "Umm al-Quwain": ("50.00", "umm_al_quwain", "third_party"),
}

# Frozen at the shape this migration published — see the note in 055. The pin
# correction in 055 and the Sharjah split in 057 both changed what the builder
# emits, and a migration that re-reads the current output no longer reproduces
# the map it is named after.
GEOJSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "uae_delivery_zones.v1.geojson.json"
)


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    lngs: list[float] = []
    lats: list[float] = []
    polys = (
        geometry["coordinates"]
        if geometry["type"] == "MultiPolygon"
        else [geometry["coordinates"]]
    )
    for poly in polys:
        for ring in poly:
            for lng, lat in ring:
                lngs.append(lng)
                lats.append(lat)
    return min(lats), max(lats), min(lngs), max(lngs)


def upgrade() -> None:
    # Existing rows predate the idea of a courier and were all served the
    # manual way, so third_party is the honest default rather than a guess.
    op.add_column(
        "delivery_polygons",
        sa.Column(
            "fulfilment_provider",
            sa.String(length=20),
            nullable=False,
            server_default="third_party",
        ),
    )

    if not GEOJSON_PATH.exists():
        raise RuntimeError(
            f"Delivery zone geometry missing at {GEOJSON_PATH}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOJSON_PATH.read_text())}
    missing = set(ZONES) - set(shapes)
    if missing:
        raise RuntimeError(f"No geometry for delivery zones: {sorted(missing)}")

    conn = op.get_bind()
    version_id = uuid.uuid4()

    # One active map at a time is a partial unique index, so the old one has to
    # step down before the new one can be published.
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = false WHERE is_active"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO delivery_polygon_versions (id, name, notes, is_active, activated_at) "
            "VALUES (:id, :name, :notes, true, now())"
        ),
        {
            "id": str(version_id),
            "name": VERSION_NAME,
            "notes": (
                "Emirate outlines (geoBoundaries gbOpen ARE ADM1, CC-BY 4.0) clipped to the "
                "reach the live Lalamove rate card was measured over: Sharjah 25 km, Ajman "
                "30 km, Dubai 40 km from the Al Qasimia kitchen. Cities are served by "
                "Lalamove at 15/15/25 AED; every remaining area stays third-party at 50 AED."
            ),
        },
    )

    for order, (name, (fee, slug, provider)) in enumerate(ZONES.items()):
        geometry = shapes[name]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, region_slug, delivery_fee, fulfilment_provider, "
                " geometry, min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :slug, :fee, :provider, "
                " CAST(:geometry AS jsonb), :min_lat, :max_lat, :min_lng, :max_lng, :display_order)"
            ),
            {
                "id": str(uuid.uuid4()),
                "version_id": str(version_id),
                "name": name,
                "slug": slug,
                "fee": fee,
                "provider": provider,
                "geometry": json.dumps(geometry),
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "display_order": order,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM delivery_polygon_versions WHERE name = :name"),
        {"name": VERSION_NAME},
    )
    # Hand the map back to whichever version 048 seeded; without this the
    # storefront would price every address at the default fee.
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true "
            "WHERE id = (SELECT id FROM delivery_polygon_versions ORDER BY created_at LIMIT 1)"
        )
    )
    op.drop_column("delivery_polygons", "fulfilment_provider")
