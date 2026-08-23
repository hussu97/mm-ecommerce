"""Publish delivery zones calculated from the corrected Sharjah kitchen pin.

The original Lalamove map was calculated from the longitude in a Google Maps
viewport, rather than the longitude of the Melting Moments Cakes place pin.
The difference is roughly 260 metres: small enough not to change the rate
card, but large enough to misrepresent the kitchen as the centre of every
courier range. Publish a fresh immutable map version so existing orders retain
their historical geometry while all new quotes use the corrected origin.

Revision ID: 055_correct_sharjah_kitchen_pin
Revises: 054_seo_content_refresh
Create Date: 2026-08-03
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "055_correct_sharjah_kitchen_pin"
down_revision: Union[str, None] = "054_seo_content_refresh"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "Lalamove strategy v1.1 — corrected Sharjah kitchen pin"

ZONES: dict[str, tuple[str, str]] = {
    "Sharjah City": ("15.00", "lalamove"),
    "Ajman City": ("15.00", "lalamove"),
    "Dubai City": ("25.00", "lalamove"),
    "Sharjah": ("50.00", "third_party"),
    "Ajman": ("50.00", "third_party"),
    "Dubai": ("50.00", "third_party"),
    "Abu Dhabi": ("50.00", "third_party"),
    "Ras al-Khaimah": ("50.00", "third_party"),
    "Fujairah": ("50.00", "third_party"),
    "Umm al-Quwain": ("50.00", "third_party"),
}

# The map as it stood when this migration was written, frozen. 057 cut Sharjah
# City open to make room for Sharjah Central, and a migration that seeds itself
# from whatever the builder last emitted stops reproducing the map it published:
# on a database built from scratch this one would seed a Sharjah City with a
# 10 km hole in it and nothing filling the hole, so a downgrade to here would
# quietly price Al Qasimia at the outside-every-zone fee.
GEOJSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "uae_delivery_zones.v1.geojson.json"
)


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    lngs: list[float] = []
    lats: list[float] = []
    for polygon in geometry["coordinates"]:
        for ring in polygon:
            for lng, lat in ring:
                lngs.append(lng)
                lats.append(lat)
    return min(lats), max(lats), min(lngs), max(lngs)


def upgrade() -> None:
    shapes = {
        zone["name"]: zone["geometry"] for zone in json.loads(GEOJSON_PATH.read_text())
    }
    missing = set(ZONES) - set(shapes)
    if missing:
        raise RuntimeError(f"No geometry for delivery zones: {sorted(missing)}")

    conn = op.get_bind()
    # `K001` is the active Sharjah kitchen that `resolve_pickup` selects for
    # Lalamove. Keep the operational pickup record in sync with the geometry
    # source so a fresh production database cannot revive the old viewport
    # longitude or vague address.
    conn.execute(
        sa.text(
            "UPDATE branches SET address = :address, latitude = :latitude, longitude = :longitude "
            "WHERE reference = :reference AND deleted_at IS NULL"
        ),
        {
            "reference": "K001",
            "address": "Melting Moments Cakes, Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah",
            "latitude": 25.3304139,
            "longitude": 55.3736131,
        },
    )
    version_id = str(uuid.uuid4())
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
            "id": version_id,
            "name": VERSION_NAME,
            "notes": (
                "Same Lalamove rate-card geometry as v1, recalculated from the verified "
                "Melting Moments Cakes pin at 25.3304139, 55.3736131."
            ),
        },
    )
    for order, (name, (fee, provider)) in enumerate(ZONES.items()):
        geometry = shapes[name]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, delivery_fee, fulfilment_provider, "
                "geometry, min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :fee, :provider, "
                "CAST(:geometry AS jsonb), :min_lat, :max_lat, :min_lng, :max_lng, :display_order)"
            ),
            {
                "id": str(uuid.uuid4()),
                "version_id": version_id,
                "name": name,
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
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true WHERE name = :name"
        ),
        {"name": "Lalamove strategy v1"},
    )
