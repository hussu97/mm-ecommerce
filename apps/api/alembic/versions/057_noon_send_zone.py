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

**And the batch schedule comes with it.** Publishing a map as fresh polygon rows
orphans the `delivery_batch_windows` that hung off the old ones, and a zone with
no windows is not an error state: `find_window` returns `None`, every order
falls through to the single-order path, and every one of them still arrives. The
failure is purely economic — roughly three times the per-delivery cost — and
completely silent. `055` did exactly this and the live map has carried no
schedule since. So the windows are copied across by zone name, and where there
is nothing to copy from, `052`'s six are seeded.

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

#: `052`'s schedule, used only when there is nothing to copy — a database built
#: from scratch, where the previous map never had windows either. They tighten
#: through the evening because that is where the orders are.
DEFAULT_WINDOWS = [
    ("Batch 1", (0, 0), (12, 0)),
    ("Batch 2", (12, 0), (18, 0)),
    ("Batch 3", (18, 0), (21, 0)),
    ("Batch 4", (21, 0), (22, 0)),
    ("Batch 5", (22, 0), (23, 0)),
    ("Batch 6", (23, 0), (24, 0)),
]

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

    _carry_over_batch_windows(conn, version_id)


def _carry_over_batch_windows(conn, version_id: str) -> None:
    """
    Give the new map the schedule the old one had.

    Matched by zone name rather than by id, because the whole point of a new
    version is that the ids are new. A zone that gains a schedule it never had —
    `Sharjah Central` is new — takes the default, and only Lalamove zones get
    one at all: a third-party zone has no run of ours to share, and noon Send's
    shared run is a separate product we do not use.
    """
    rows = conn.execute(
        sa.text(
            "SELECT p.id, p.name FROM delivery_polygons p "
            "WHERE p.version_id = :version_id "
            "AND p.fulfilment_provider = 'lalamove'"
        ),
        {"version_id": version_id},
    ).fetchall()

    for polygon_id, name in rows:
        previous = conn.execute(
            sa.text(
                "SELECT w.label, w.start_hour, w.start_minute, w.end_hour, "
                "       w.end_minute, w.is_active "
                "FROM delivery_batch_windows w "
                "JOIN delivery_polygons p ON p.id = w.polygon_id "
                "WHERE p.name = :name AND p.version_id <> :version_id "
                "ORDER BY p.created_at DESC, w.start_hour, w.start_minute"
            ),
            {"name": name, "version_id": version_id},
        ).fetchall()

        windows = (
            [(r[0], (r[1], r[2]), (r[3], r[4]), r[5]) for r in previous]
            if previous
            else [(label, start, end, True) for label, start, end in DEFAULT_WINDOWS]
        )
        # A zone can appear on several old versions; take one schedule, not one
        # per version it ever had.
        seen: set[str] = set()
        for label, (start_hour, start_minute), (
            end_hour,
            end_minute,
        ), active in windows:
            if label in seen:
                continue
            seen.add(label)
            conn.execute(
                sa.text(
                    "INSERT INTO delivery_batch_windows "
                    "(id, polygon_id, label, start_hour, start_minute, end_hour, "
                    " end_minute, is_active) "
                    "VALUES (:id, :polygon_id, :label, :start_hour, :start_minute, "
                    " :end_hour, :end_minute, :is_active)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "polygon_id": str(polygon_id),
                    "label": label,
                    "start_hour": start_hour,
                    "start_minute": start_minute,
                    "end_hour": end_hour,
                    "end_minute": end_minute,
                    "is_active": active,
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
