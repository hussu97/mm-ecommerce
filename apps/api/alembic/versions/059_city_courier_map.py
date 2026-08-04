"""Courier and free delivery in the three city zones; flat third-party beyond.

057 handed the whole country to Lalamove and priced everything outside the city
zones from a live courier quote. That answered the pricing question honestly —
the flat 50 really was under-charging a 137 dirham Abu Dhabi run — but it bought
the honesty with three costs the shop did not want:

  * A quote the customer waits for on every pin change, and a checkout that has
    to refuse an address outright when the courier will not answer.
  * A courier booking, and its wallet, for runs an existing third-party provider
    already covers more cheaply at that distance.
  * A price that moves between the moment it is shown and the moment the order
    is written.

So the map goes back to two kinds of zone, and this time each kind is described
by its own flags rather than inferred from one of them:

  Sharjah City (25 km)   15 AED  lalamove     batched      free over threshold
  Ajman City   (30 km)   15 AED  lalamove     batched      free over threshold
  Dubai City   (40 km)   25 AED  lalamove     batched      free over threshold
  everything else        80 AED  third_party  not batched  never free

Every zone is fixed-fee again, so nothing is ever refused for want of a quote,
and `default_delivery_fee` moves to 80 to match the outer zones — a pin outside
every drawn shape is an outer pin in all but name.

**Free delivery** gets a column of its own (`free_delivery_eligible`, added
here). It was being read off `pricing_mode`, which was correct only while
"fixed fee" and "we deliver it ourselves" happened to name the same three zones.
This migration makes them different sets, and the old proxy would have started
giving free delivery away in the 80 AED zones — the most expensive places we go.

**The city schedule** is rebuilt around the shop's hours. Batch 1 now opens at
23:00 the night before instead of midnight, so an order placed after closing
joins the next morning's run rather than falling through a gap; and the last
batch closes at 23:00 rather than midnight, because dispatching after the store
has shut sends a driver to a dark kitchen. The five slots still tile the whole
24 hours with no overlap and no hole:

    Batch 1  23:00 → 12:00   (wraps midnight)
    Batch 2  12:00 → 18:00
    Batch 3  18:00 → 21:00
    Batch 4  21:00 → 22:30
    Batch 5  22:30 → 23:00

Outer zones get no windows at all. They are not ours to dispatch, so a schedule
on one would be a setting that does nothing — and a setting that does nothing is
worse than an absent one, because somebody will eventually rely on it.

058's map is left in place and merely deactivated, so rolling back is pointing
`is_active` at it again.

Revision ID: 059_city_courier_map
Revises: 058_free_delivery_scope
Create Date: 2026-08-03
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "059_city_courier_map"
down_revision: Union[str, None] = "058_free_delivery_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


VERSION_NAME = "City courier, third-party beyond v1"
PREVIOUS_VERSION_NAME = "Lalamove everywhere v1"

OUTER_FEE = "80.00"

#: name -> (fee, provider, free delivery). Cities first so the smaller shape
#: wins the lookup against the emirate it sits inside.
ZONES: list[tuple[str, str, str, bool]] = [
    ("Sharjah City", "15.00", "lalamove", True),
    ("Ajman City", "15.00", "lalamove", True),
    ("Dubai City", "25.00", "lalamove", True),
    ("Sharjah", OUTER_FEE, "third_party", False),
    ("Ajman", OUTER_FEE, "third_party", False),
    ("Dubai", OUTER_FEE, "third_party", False),
    ("Abu Dhabi", OUTER_FEE, "third_party", False),
    ("Ras al-Khaimah", OUTER_FEE, "third_party", False),
    ("Fujairah", OUTER_FEE, "third_party", False),
    ("Umm al-Quwain", OUTER_FEE, "third_party", False),
]

#: Only the zones we dispatch ourselves get one. 23:00 to 23:00, tiling the day.
CITY_WINDOWS: list[tuple[str, tuple[int, int], tuple[int, int]]] = [
    ("Batch 1", (23, 0), (12, 0)),
    ("Batch 2", (12, 0), (18, 0)),
    ("Batch 3", (18, 0), (21, 0)),
    ("Batch 4", (21, 0), (22, 30)),
    ("Batch 5", (22, 30), (23, 0)),
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
    # False by default: a zone drawn without anyone thinking about the offer
    # should not be making it.
    op.add_column(
        "delivery_polygons",
        sa.Column(
            "free_delivery_eligible",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )

    if not GEOJSON_PATH.exists():
        raise RuntimeError(
            f"Delivery zone geometry missing at {GEOJSON_PATH}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOJSON_PATH.read_text())}
    missing = {name for name, _, _, _ in ZONES} - set(shapes)
    if missing:
        raise RuntimeError(f"No geometry for delivery zones: {sorted(missing)}")

    conn = op.get_bind()
    version_id = uuid.uuid4()

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
                "Sharjah City (25 km), Ajman City (30 km) and Dubai City (40 km) from the Al "
                "Qasimia kitchen are carried by Lalamove at 15/15/25 AED, batched on the "
                "23:00–23:00 schedule, and deliver free above the threshold. Every remaining "
                "area is third-party at a flat 80 AED, unbatched, and never free."
            ),
        },
    )

    for display_order, (name, fee, provider, free) in enumerate(ZONES):
        geometry = shapes[name]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        polygon_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, delivery_fee, pricing_mode, free_delivery_eligible, "
                " fulfilment_provider, geometry, min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :fee, 'static', :free, :provider, "
                " CAST(:geometry AS jsonb), :min_lat, :max_lat, :min_lng, :max_lng, :display_order)"
            ),
            {
                "id": str(polygon_id),
                "version_id": str(version_id),
                "name": name,
                "fee": fee,
                "free": free,
                "provider": provider,
                "geometry": json.dumps(geometry),
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "display_order": display_order,
            },
        )

        if provider != "lalamove":
            continue
        for label, (start_h, start_m), (end_h, end_m) in CITY_WINDOWS:
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

    # A pin outside every drawn shape is an outer pin in all but name.
    op.execute(
        sa.text(f"UPDATE delivery_settings SET default_delivery_fee = {OUTER_FEE}")
    )
    op.alter_column(
        "delivery_settings", "default_delivery_fee", server_default=OUTER_FEE
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM delivery_polygon_versions WHERE name = :name"),
        {"name": VERSION_NAME},
    )
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true WHERE id = ("
            "  SELECT id FROM delivery_polygon_versions"
            "  WHERE name = :name ORDER BY created_at DESC LIMIT 1"
            ")"
        ),
        {"name": PREVIOUS_VERSION_NAME},
    )
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true WHERE NOT EXISTS ("
            "  SELECT 1 FROM delivery_polygon_versions WHERE is_active"
            ") AND id = (SELECT id FROM delivery_polygon_versions ORDER BY created_at LIMIT 1)"
        )
    )
    op.execute(sa.text("UPDATE delivery_settings SET default_delivery_fee = 50.00"))
    op.alter_column("delivery_settings", "default_delivery_fee", server_default="50.00")
    op.drop_column("delivery_polygons", "free_delivery_eligible")
