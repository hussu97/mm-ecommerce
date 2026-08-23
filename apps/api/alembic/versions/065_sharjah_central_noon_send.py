"""Cut Sharjah once more so its inner 20 km is carried by noon Send.

`059` settled the shape of the map: three fixed-fee city zones we dispatch
ourselves, free above the threshold, and a flat 80 AED third-party remainder
that never is. Nothing about that changes here. What changes is who carries the
inner part of Sharjah.

noon Send (noon's Rider-on-Demand fleet) is a second API-booked courier. On a
bike it charges AED 12 flat to 10 road km, then +1.00/km to 15 and +1.50/km to
20, with +1 during the 12:00-15:00 and 19:00-22:00 surge windows. Against
Lalamove's `17 + 0.70/km` it is cheaper at every distance it will carry:

    road km      5      10      15      20
    bike     12.00   12.00   17.00   24.50
    +surge   13.00   13.00   18.00   25.50
    Lalamove 20.50   24.00   27.50   31.00

Price therefore does not decide the boundary. Two hard limits do: noon Send
cannot cross an emirate boundary, and it will not carry a run past 20 km. From
the Al Qasimia kitchen that means Sharjah, and only the inner part of it. Road
distance runs about 1.49x straight line across the sixteen Sharjah areas the
Lalamove rate card was measured over, so 20 road km is a 13.4 km circle. Sharjah
therefore becomes three zones instead of two:

  Sharjah Central (13.4 km)  15 AED  noon_send    free  Al Khan .. University City
  Sharjah City    (25 km)    15 AED  lalamove     free  Al Zahia .. Al Rahmaniya
  Sharjah         (rest)     80 AED  third_party  -     Al Dhaid, Khor Fakkan, Kalba

The customer sees none of it. A basket delivered to Al Qasimia pays the same AED
15 it paid yesterday and still delivers free over the threshold; what changes is
that the run behind it costs us 12 instead of 24. The name says a place, not a
carrier, because `zone_name` reaches the storefront.

**And it is a trial.** `courier_service` only actually books noon Send for the
trial account; every other order in Sharjah Central falls back to Lalamove and
is charged and delivered exactly as before. The map is drawn for the end state
so that lifting the gate is one deploy rather than a new migration - but until
it is lifted, this migration changes nobody's fee and nobody's courier.

Everything outside Sharjah is carried across untouched: noon Send cannot serve
it, so Ajman City and Dubai City stay on Lalamove at 15 and 25 AED and the outer
zones stay third-party at 80.

**The batch schedule comes with it.** Publishing a map as fresh polygon rows
orphans the `delivery_batch_windows` that hung off the old ones, and a zone with
no windows is not an error state: `find_window` returns `None`, every order
falls through to the single-order path, and every one of them still arrives. The
failure is purely economic - roughly three times the per-delivery cost - and
completely silent. `055` did exactly this once already. So `059`'s five windows
are recreated on the Lalamove zones here.

Sharjah Central deliberately gets none. noon Send is never batched: their shared
run is a different product with a cap of three that we do not use, so those
orders always go out alone - which is exactly what lets the zone promise an
hour.

`059`'s map is left in place and merely deactivated, so rolling back is pointing
`is_active` at it again.

Revision ID: 065_sharjah_central_noon_send
Revises: 064_polygon_branch
Create Date: 2026-08-04
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "065_sharjah_central_noon_send"
down_revision: Union[str, None] = "064_polygon_branch"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "noon Send strategy v1 - Sharjah Central"
PREVIOUS_VERSION_NAME = "City courier, third-party beyond v1"

OUTER_FEE = "80.00"

#: The kitchen every zone is served from today. Set here as well as in `064`
#: because these are new rows: a polygon written without a branch reaches no
#: register, and the fallback that covers that case is a safety net rather than
#: somewhere to land by design.
SHARJAH_BRANCH_REF = "K001"

#: name -> (fee, provider, free delivery). Insertion order is `display_order`,
#: and `find_zone` takes the first match, so the inner zones come first. The
#: geometry is already disjoint - the builder punches each smaller circle out of
#: the shape around it - but the ordering is kept correct anyway so neither one
#: alone is load-bearing.
ZONES: list[tuple[str, str, str, bool]] = [
    ("Sharjah Central", "15.00", "noon_send", True),
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

#: `059`'s schedule, verbatim. 23:00 to 23:00, tiling the day with no overlap
#: and no hole, and only ever on the zones we batch.
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
    if not GEOJSON_PATH.exists():
        raise RuntimeError(
            f"Delivery zone geometry missing at {GEOJSON_PATH}. "
            "Regenerate it with `python -m scripts.build_delivery_zones`."
        )

    shapes = {z["name"]: z["geometry"] for z in json.loads(GEOJSON_PATH.read_text())}
    missing = {name for name, _, _, _ in ZONES} - set(shapes)
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

    # One active map at a time is a partial unique index, so the old one steps
    # down before the new one is published.
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
                "059's map with Sharjah cut once more at 13.4 km - noon Send's 20 km road "
                "ceiling over the 1.49x road-to-crow ratio. Inside that circle noon Send "
                "charges AED 12-24.50 against Lalamove's 20.50-31; outside it Sharjah City "
                "stays on Lalamove. Fees, free-delivery scope and the batch schedule are "
                "unchanged everywhere, and noon Send is gated to the trial account until it "
                "moves off staging."
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
                " fulfilment_provider, branch_id, geometry, min_lat, max_lat, min_lng, "
                " max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :fee, 'static', :free, :provider, "
                " :branch_id, CAST(:geometry AS jsonb), :min_lat, :max_lat, :min_lng, "
                " :max_lng, :display_order)"
            ),
            {
                "id": str(polygon_id),
                "version_id": str(version_id),
                "name": name,
                "fee": fee,
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
    # Hand the map back to 059's, without which the storefront would price every
    # address at the default fee.
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
