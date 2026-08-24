"""Lalamove everywhere: fixed fees around the kitchen, courier's own price beyond it.

Version 050 split the country in two: three city zones served by Lalamove at
15/15/25, and every remaining area at a flat 50 carried by whoever we already
used. Both halves of that have stopped being true.

Lalamove serves the whole of the UAE, so there is no longer a reason for a
second, manual fulfilment path — every zone on this map is carried by the
courier, and "third party" survives only as a setting nothing on the live map
uses.

The flat 50 was always a compromise. A run to the far side of Sharjah and a run
to Abu Dhabi are not the same journey and never cost the same; one number for
both overcharges the near half of every zone and loses money on the far half.
So the map now carries *two* ways of pricing:

  Sharjah City (25 km)  static   15 AED
  Ajman City   (30 km)  static   15 AED
  Dubai City   (40 km)  static   25 AED
  everything else       dynamic  the courier's own quote, rounded up

Static means what it always meant — a published fee, fixed, absorbing whatever
an individual run turns out to cost. Those three zones are dense, close, and
well enough measured to keep doing that.

Dynamic means the customer is quoted the courier's price for their exact pin,
rounded up to the whole dirham. The consequence worth stating plainly: a pin the
courier will not quote has no price at all, and checkout refuses it rather than
inventing one. That is the intended behaviour — an order we cannot deliver is
worse than an order we do not take.

Dynamic zones keep a `delivery_fee` of zero rather than an inherited 50. Nothing
reads it, and a plausible-looking number in a column nothing charges is exactly
how someone later concludes we charge 50 in Abu Dhabi.

Batch windows are seeded for every zone on the new map. 052 seeded them against
the polygons that existed when it ran, and 055 then republished the whole map as
new rows — so the live zones have carried no schedule at all since, and every
order has been dispatched on its own. Publishing a map without seeding its
schedule is the bug; doing it here is the fix:

  static zones   00:00–12:00, 12:00–18:00, 18:00–21:00, 21:00–22:30, 22:30–24:00
  dynamic zones  00:00–17:00, 17:00–24:00

050's map is left in place and merely deactivated, so rolling back is pointing
`is_active` at it again.

Revision ID: 057_dynamic_delivery_pricing
Revises: 056_correct_barsha_heights_pin
Create Date: 2026-08-03
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "057_dynamic_delivery_pricing"
down_revision: Union[str, None] = "056_correct_barsha_heights_pin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VERSION_NAME = "Lalamove everywhere v1"
PREVIOUS_VERSION_NAME = "Lalamove strategy v1.1 — corrected Sharjah kitchen pin"

#: zone name -> (fee, pricing mode). Every zone is carried by Lalamove.
ZONES: list[tuple[str, str, str]] = [
    ("Sharjah City", "15.00", "static"),
    ("Ajman City", "15.00", "static"),
    ("Dubai City", "25.00", "static"),
    ("Sharjah", "0.00", "dynamic"),
    ("Ajman", "0.00", "dynamic"),
    ("Dubai", "0.00", "dynamic"),
    ("Abu Dhabi", "0.00", "dynamic"),
    ("Ras al-Khaimah", "0.00", "dynamic"),
    ("Fujairah", "0.00", "dynamic"),
    ("Umm al-Quwain", "0.00", "dynamic"),
]

#: The city zones fill a van fast and are cheap to reach, so they are cut fine —
#: five slots, tightening through the evening when most orders land.
STATIC_WINDOWS: list[tuple[str, tuple[int, int], tuple[int, int]]] = [
    ("Batch 1", (0, 0), (12, 0)),
    ("Batch 2", (12, 0), (18, 0)),
    ("Batch 3", (18, 0), (21, 0)),
    ("Batch 4", (21, 0), (22, 30)),
    ("Batch 5", (22, 30), (24, 0)),
]

#: Everywhere else fills slowly and costs a great deal to reach, so waiting is
#: worth far more there than promptness is. Two slots a day.
DYNAMIC_WINDOWS: list[tuple[str, tuple[int, int], tuple[int, int]]] = [
    ("Batch 1", (0, 0), (17, 0)),
    ("Batch 2", (17, 0), (24, 0)),
]

GEOJSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "uae_delivery_zones.v2.geojson.json"
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
    # Rows that predate the idea of two pricing strategies were all fixed-fee,
    # so static is the honest default rather than a guess.
    op.add_column(
        "delivery_polygons",
        sa.Column(
            "pricing_mode",
            sa.String(length=10),
            nullable=False,
            server_default="static",
        ),
    )

    if not GEOJSON_PATH.exists():
        raise RuntimeError(
            f"Delivery zone geometry missing at {GEOJSON_PATH}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOJSON_PATH.read_text())}
    missing = {name for name, _, _ in ZONES} - set(shapes)
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
                "Every zone is carried by Lalamove. The three city zones around the Al "
                "Qasimia kitchen (Sharjah 25 km, Ajman 30 km, Dubai 40 km) keep fixed fees "
                "of 15/15/25 AED. Every remaining area is priced from the courier's own "
                "quote for the customer's pin, rounded up to the whole dirham; a pin that "
                "cannot be quoted cannot be ordered to."
            ),
        },
    )

    for display_order, (name, fee, pricing_mode) in enumerate(ZONES):
        geometry = shapes[name]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        polygon_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, delivery_fee, pricing_mode, fulfilment_provider, "
                " geometry, min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :fee, :pricing_mode, 'lalamove', "
                " CAST(:geometry AS jsonb), :min_lat, :max_lat, :min_lng, :max_lng, :display_order)"
            ),
            {
                "id": str(polygon_id),
                "version_id": str(version_id),
                "name": name,
                "fee": fee,
                "pricing_mode": pricing_mode,
                "geometry": json.dumps(geometry),
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "display_order": display_order,
            },
        )

        windows = STATIC_WINDOWS if pricing_mode == "static" else DYNAMIC_WINDOWS
        for label, (start_h, start_m), (end_h, end_m) in windows:
            conn.execute(
                sa.text(
                    "INSERT INTO delivery_batch_windows "
                    "(id, polygon_id, label, start_hour, start_minute, end_hour, end_minute) "
                    "VALUES (:id, :polygon_id, :label, :sh, :sm, :eh, :em)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "polygon_id": str(polygon_id),
                    "label": label,
                    "sh": start_h,
                    "sm": start_m,
                    "eh": end_h,
                    "em": end_m,
                },
            )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM delivery_polygon_versions WHERE name = :name"),
        {"name": VERSION_NAME},
    )
    # Hand the map back to 050's, by name; without this the storefront would
    # price every address at the default fee.
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true WHERE id = ("
            "  SELECT id FROM delivery_polygon_versions"
            "  WHERE name = :name ORDER BY created_at DESC LIMIT 1"
            ")"
        ),
        {"name": PREVIOUS_VERSION_NAME},
    )
    # If 050's map is gone too, fall back to the oldest surviving one rather
    # than leaving the country unpriced.
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true WHERE NOT EXISTS ("
            "  SELECT 1 FROM delivery_polygon_versions WHERE is_active"
            ") AND id = (SELECT id FROM delivery_polygon_versions ORDER BY created_at LIMIT 1)"
        )
    )
    op.drop_column("delivery_polygons", "pricing_mode")
