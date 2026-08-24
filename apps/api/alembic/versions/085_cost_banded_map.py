"""Publish the cost-banded map, and give each zone its own free-delivery threshold.

Fifteen zones cut where a courier run changes price rather than where an emirate
ends, replacing eleven cut per emirate. The geometry comes from
`scripts.build_delivery_zones`; this migration only prices it.

The fee ladder, and why each number is what it is — courier costs are measured,
un-batched, from the Sharjah kitchen:

    Sharjah Central       free    always   noon Send  costs 13.60 on a bike
    Sharjah Outer          20      >75     Lalamove   costs 32 (Al Rahmaniya, 21 km)
    Ajman City             10      >75     Lalamove   costs 28
    Dubai Near/Mid/Far     20      >75     Lalamove   costs 24-59
    Umm al-Quwain City     30      >75     Lalamove   costs 44
    Ras al-Khaimah City    50      >100    Lalamove   costs 73
    everywhere else        80      >200    3rd party  costs 80

Two of these are deliberately below cost and are not mistakes. Dubai averages 42
to reach and charges 20, free over 75; Umm al-Quwain costs 44 and charges 30. The
gap is bought basket growth, and the number that makes it defensible is the 37%
an aggregator takes instead — at a 75 basket that is AED 27.75 we keep, against a
delivery we are subsidising by roughly the same. The three Dubai bands carry one
fee today and exist so that repricing the far half later is an admin edit rather
than a migration that redraws the map.

Sharjah Central is free at any basket, which is why its threshold is 0.00 rather
than NULL. NULL means "use the national number"; zero means "there is no bar to
clear", and the two must not be confused.

Revision ID: 085_cost_banded_map
Revises: 084_low_order_fee
Create Date: 2026-08-07
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "085_cost_banded_map"
down_revision: Union[str, None] = "084_low_order_fee"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "Cost-banded map v1"
PREVIOUS_VERSION_NAME = "noon Send strategy v1 - Sharjah Central"

OUTER_FEE = "80.00"
OUTER_THRESHOLD = "200.00"

SHARJAH_BRANCH_REF = "K001"

#: name -> (fee, free-delivery threshold, provider, free delivery offered).
#: Insertion order is `display_order`, and `find_zone` takes the first match, so
#: the bands run inner to outer. That ordering is load-bearing now in a way it
#: was not before: the innermost Sharjah band is built as a circle minus its
#: neighbours so that gaps in the source outlines still get served, and a gap is
#: therefore claimed by the smallest band that reaches it.
ZONES: list[tuple[str, str, str, str, bool]] = [
    ("Sharjah Central", "0.00", "0.00", "noon_send", True),
    ("Dubai Near", "20.00", "75.00", "lalamove", True),
    ("Ajman City", "10.00", "75.00", "lalamove", True),
    ("Sharjah Outer", "20.00", "75.00", "lalamove", True),
    ("Dubai Mid", "20.00", "75.00", "lalamove", True),
    ("Umm al-Quwain City", "30.00", "75.00", "lalamove", True),
    ("Dubai Far", "20.00", "75.00", "lalamove", True),
    ("Ras al-Khaimah City", "50.00", "100.00", "lalamove", True),
    ("Abu Dhabi", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Ajman", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Dubai", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Fujairah", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Ras al-Khaimah", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Sharjah", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Umm al-Quwain", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
]

#: Unchanged from `059`. 23:00 to 23:00, tiling the day with no overlap and no
#: hole, and only ever on the zones we batch.
CITY_WINDOWS: list[tuple[str, tuple[int, int], tuple[int, int]]] = [
    ("Batch 1", (23, 0), (12, 0)),
    ("Batch 2", (12, 0), (18, 0)),
    ("Batch 3", (18, 0), (21, 0)),
    ("Batch 4", (21, 0), (22, 30)),
    ("Batch 5", (22, 30), (23, 0)),
]

#: A frozen snapshot, not the file the generator writes.
#:
#: Migrations 057, 059 and 065 all read the generator's live output by name, so
#: regenerating it for this change broke `alembic upgrade head` on an empty
#: database — the shape they were written against no longer existed under the
#: names they ask for. CI builds exactly that way on every merge.
#:
#: A migration is a historical record and has to keep meaning what it meant when
#: it was written, so it reads a `.vN.` copy that nothing regenerates.
#: `test_migrations_read_frozen_geometry` enforces it.
GEOJSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "uae_delivery_zones.v3.geojson.json"
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
    if not GEOJSON_PATH.exists():
        raise RuntimeError(
            f"Delivery zone geometry missing at {GEOJSON_PATH}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOJSON_PATH.read_text())}
    missing = {name for name, *_ in ZONES} - set(shapes)
    if missing:
        raise RuntimeError(
            f"No geometry for delivery zones: {sorted(missing)}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    conn = op.get_bind()
    version_id = uuid.uuid4()

    branch_id = conn.execute(
        sa.text("SELECT id FROM branches WHERE reference = :ref"),
        {"ref": SHARJAH_BRANCH_REF},
    ).scalar()

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
                "Zones cut where a courier run changes price rather than where an "
                "emirate ends. Sharjah splits at noon Send's 20 km reach and is free "
                "inside it; Dubai splits into three bands carrying one fee for now; "
                "Umm al-Quwain and Ras al-Khaimah get served bands where a Lalamove "
                "car beats the third-party flat 80. Free-delivery thresholds are "
                "per-zone for the first time: 75 across the city zones, 100 in Ras "
                "al-Khaimah, 200 beyond."
            ),
        },
    )

    for display_order, (name, fee, threshold, provider, free) in enumerate(ZONES):
        geometry = shapes[name]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        polygon_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, delivery_fee, free_delivery_threshold, "
                " pricing_mode, free_delivery_eligible, fulfilment_provider, branch_id, "
                " geometry, min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :fee, :threshold, 'static', :free, "
                " :provider, :branch_id, CAST(:geometry AS jsonb), :min_lat, :max_lat, "
                " :min_lng, :max_lng, :display_order)"
            ),
            {
                "id": str(polygon_id),
                "version_id": str(version_id),
                "name": name,
                "fee": fee,
                "threshold": threshold,
                "free": free,
                "provider": provider,
                "branch_id": str(branch_id) if branch_id else None,
                "geometry": json.dumps(geometry),
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "display_order": display_order,
            },
        )

        # Lalamove only. A third-party zone has no run of ours to share, and a
        # noon Send order never shares one at all.
        if provider != "lalamove":
            continue
        for label, (start_h, start_m), (end_h, end_m) in CITY_WINDOWS:
            conn.execute(
                sa.text(
                    "INSERT INTO delivery_batch_windows "
                    "(id, polygon_id, label, start_hour, start_minute, end_hour, "
                    " end_minute, is_active) "
                    "VALUES (:id, :polygon_id, :label, :sh, :sm, :eh, :em, true)"
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
    """Delete this map and put the previous one back in force."""
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM delivery_polygon_versions WHERE name = :name"),
        {"name": VERSION_NAME},
    )
    conn.execute(
        sa.text(
            "UPDATE delivery_polygon_versions SET is_active = true, activated_at = now() "
            "WHERE name = :name"
        ),
        {"name": PREVIOUS_VERSION_NAME},
    )
