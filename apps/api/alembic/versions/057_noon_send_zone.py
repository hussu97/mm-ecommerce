"""Split Sharjah so its inner half is carried by noon Send.

Every zone until now was either Lalamove or a third party. noon Send (noon's
Rider-on-Demand fleet) is a second API-booked courier and it is cheaper than
Lalamove on everything it can reach — AED 12 flat to 10 road km against
Lalamove's `17 + 0.70/km`, and still ahead out to 31 road km, which is past the
far edge of Sharjah City.

Price therefore does not decide the boundary. Two hard limits do: noon Send
cannot cross an emirate boundary, and it caps a run at 15 km. From the Al
Qasimia kitchen that means Sharjah only, and only the inner part of it. Road
distance runs about 1.49x straight line across the sixteen Sharjah areas the
live Lalamove rate card was measured over, so 15 road km is a 10 km circle.

So Sharjah becomes three zones instead of two:

  Sharjah Central (10 km)  15 AED  noon_send    Al Khan .. Muwaileh (12.8 road km)
  Sharjah City    (25 km)  15 AED  lalamove     Al Zahia .. Al Rahmaniya
  Sharjah          (rest)  50 AED  third_party  Al Dhaid, Khor Fakkan, Kalba

The fee does not change anywhere. A customer in Al Qasimia pays the same AED 15
they paid yesterday; what changes is that the run behind it costs us 12 instead
of 24. Nor is the split visible to them — `Sharjah Central` names a place, not a
carrier, because the zone name reaches the storefront.

Everything outside Sharjah is carried across untouched: noon Send cannot serve
it, so Ajman City and Dubai City stay on Lalamove at 15 and 25 AED.

Version 056's map is left in place and merely deactivated, so rolling back is
pointing `is_active` at it again — the same contract as 050 and 055.

Revision ID: 057_noon_send_zone
Revises: 056_correct_barsha_heights_pin
Create Date: 2026-08-04
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "057_noon_send_zone"
down_revision: Union[str, None] = "056_correct_barsha_heights_pin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "noon Send strategy v1 — Sharjah Central"
PREVIOUS_VERSION_NAME = "Lalamove strategy v1.1 — corrected Sharjah kitchen pin"

# Insertion order is `display_order`, and `find_zone` takes the first match, so
# the inner zones have to come first. The geometry is already disjoint — the
# builder punches each smaller circle out of the shape around it — but the
# ordering is kept correct anyway so neither one alone is load-bearing.
ZONES: dict[str, tuple[str, str]] = {
    "Sharjah Central": ("15.00", "noon_send"),
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

GEOJSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "uae_delivery_zones.geojson.json"
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
    if not GEOJSON_PATH.exists():
        raise RuntimeError(
            f"Delivery zone geometry missing at {GEOJSON_PATH}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    shapes = {
        zone["name"]: zone["geometry"] for zone in json.loads(GEOJSON_PATH.read_text())
    }
    missing = set(ZONES) - set(shapes)
    if missing:
        raise RuntimeError(
            f"No geometry for delivery zones: {sorted(missing)}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    conn = op.get_bind()
    version_id = str(uuid.uuid4())

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
            "id": version_id,
            "name": VERSION_NAME,
            "notes": (
                "Same emirate outlines and kitchen pin as v1.1, with Sharjah cut once more "
                "at 10 km. Inside that circle a run is about 15 road km or less, which is "
                "noon Send's hard limit, and noon Send charges AED 12 there against "
                "Lalamove's 19-26. Outside it Sharjah City stays on Lalamove; beyond 25 km "
                "the emirate stays third-party at AED 50. Fees are unchanged everywhere."
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
    # Hand the map back to 055's, without which the storefront would price every
    # address at the default fee.
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true WHERE name = :name"
        ),
        {"name": PREVIOUS_VERSION_NAME},
    )
