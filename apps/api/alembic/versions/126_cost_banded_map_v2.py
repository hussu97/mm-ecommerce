"""Re-split the delivery map, without moving a single customer's fee.

`085` drew fifteen zones as radial bands around the Sharjah kitchen, and gave
each emirate's leftover — "the emirate minus the band" — the third-party price.
That leftover inherits the emirate's exclaves, and two years of geography
arrived in one shape:

    Sharjah leftover   Al Dhaid (25 km) beside Khor Fakkan (98), Kalba (103),
                       Dibba Al Hisn (96) and Masafi (80)
    Ajman leftover     the corniche's hinterland beside Masfout (89), inland
                       near Hatta
    Dubai leftover     the southern desert beside Hatta (96)
    Umm al-Quwain      a strip stranded between the UAQ City band at 36 km and
                       the Ras al-Khaimah band at 78
    Ras al-Khaimah     one 78 km band charging 50.00 flat from 56 km to 77

Seven new bands cut those apart, and the three leftovers that are now a single
named place are renamed to say so: `Sharjah East Coast`, `Ajman Masfout`,
`Dubai Hatta`. Twenty-two zones where there were fifteen.

**No customer's fee moves, with one deliberate exception.** Every new band
carries the fee, threshold, pricing mode, free-delivery flag and branch of the
zone it was carved out of: the four `*Inland` / `Dubai Outer` bands come out of
third-party leftovers and keep 80.00 / 200.00, and the Ras al-Khaimah bands
keep 50.00 / 100.00. The exception is Ras al-Khaimah beyond 78 km — Al Rams and
the northern tip — which was leftover at 80.00 / 200.00 and is now
`Ras al-Khaimah North` at 50.00 / 100.00 on Lalamove. That is the routing the
fare survey asked for (all eight measured RAK areas go by Lalamove car) and it
is a fee going **down**, which is the only direction a surprise is acceptable
in.

**Why a migration rather than a draft published from the admin.** Swapping the
active version is two `UPDATE`s, and `uq_delivery_polygon_version_active`
permits only one active row, so they cannot be combined. A request landing
between them reads `()` from `get_active_zones`, `None` from `find_zone`, and
since `088` removed the national default fee that is a hard `UnserviceableArea`
for **every pin in the country**. A migration runs both updates in one
transaction; the admin publish path does not guarantee it. `create_version`
also drops the batch schedule on the way through — fixed in the same change,
but the map is not waiting on that fix to be trusted.

Nothing has to be restarted afterwards. `get_active_zones` re-reads
`get_active_version` on every call and caches by version id, so changing the id
misses every worker's cache and reloads it on that worker's next request.

Revision ID: 126_cost_banded_map_v2
Revises: 125_zone_alternates
Create Date: 2026-08-21
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "126_cost_banded_map_v2"
down_revision: Union[str, None] = "125_zone_alternates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VERSION_NAME = "Cost-banded map v2"
PREVIOUS_VERSION_NAME = "Cost-banded map v1"

OUTER_FEE = "80.00"
OUTER_THRESHOLD = "200.00"

SHARJAH_BRANCH_REF = "K001"

#: name -> (fee, free-delivery threshold, provider, free delivery offered).
#:
#: Insertion order is `display_order`, and `find_zone` takes the first match, so
#: the bands run inner to outer — by radius from the kitchen across all
#: emirates at once, not emirate by emirate. That ordering is load-bearing: the
#: bands are built as `circle ∩ grown-emirate − neighbours` so that gaps in the
#: source outlines still get served, and a gap is therefore claimed by the
#: smallest band that reaches it. Khalid Lagoon belongs to `Sharjah Core` at
#: 3.5 km rather than to `Ras al-Khaimah North` at 110.
#:
#: The leftovers come last, after every band, for the same reason.
ZONES: list[tuple[str, str, str, str, bool]] = [
    ("Sharjah Core", "0.00", "0.00", "noon_send", True),
    ("Sharjah Central", "0.00", "0.00", "noon_send", True),
    ("Dubai Near", "20.00", "75.00", "lalamove", True),
    ("Ajman City", "10.00", "75.00", "lalamove", True),
    ("Sharjah Outer", "20.00", "75.00", "lalamove", True),
    ("Dubai Mid", "20.00", "75.00", "lalamove", True),
    ("Umm al-Quwain City", "30.00", "75.00", "lalamove", True),
    ("Dubai Far", "20.00", "75.00", "lalamove", True),
    ("Sharjah Inland", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Ajman Inland", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Umm al-Quwain Inland", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Ras al-Khaimah South", "50.00", "100.00", "lalamove", True),
    ("Dubai Outer", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Ras al-Khaimah City", "50.00", "100.00", "lalamove", True),
    ("Ras al-Khaimah North", "50.00", "100.00", "lalamove", True),
    ("Abu Dhabi", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Ajman Masfout", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Dubai Hatta", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Fujairah", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Ras al-Khaimah", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Sharjah East Coast", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
    ("Umm al-Quwain", OUTER_FEE, OUTER_THRESHOLD, "third_party", True),
]

#: preferred courier -> where a zone's orders may be moved when it will not
#: carry them, for the zones this migration creates.
#:
#: A new map's polygons are new rows, and `125_zone_alternates` ran before this
#: one — its backfill cannot reach forward. Left to the column default every
#: zone on this map would publish with `[]`, which reads as "these orders may
#: not be moved anywhere" and is exactly the outage that migration exists to
#: prevent, reintroduced by a map republish.
#:
#: Spelled out rather than imported from `DEFAULT_ALTERNATES`, per the same
#: reasoning `125` gives: a migration has to keep describing the database as it
#: was, so it must not read a constant that will be edited later.
ALTERNATES: dict[str, str] = {
    "lalamove": '["third_party"]',
    "third_party": '["lalamove"]',
    "noon_send": '["third_party", "lalamove"]',
}

#: The one zone whose answer a per-provider matrix cannot give.
#:
#: `Sharjah Core` is carved out of `Sharjah Central`, so it is the only zone on
#: this map that is both inside noon Send's reach and destined for a different
#: courier. Its alternates say so — and once `128` hands it to Slider this is
#: also precisely what the pilot gate falls back to there, so the map and the
#: automatic routing agree rather than one of them silently refusing a move the
#: other makes on its own.
ZONE_ALTERNATES: dict[str, str] = {
    "Sharjah Core": '["third_party", "lalamove"]',
}

#: Which shared run each zone rides, by group name — the groups `088` created,
#: which belong to the schedule rather than to a map version and so survive
#: this republish untouched.
#:
#: A new map's polygons are new rows, and a new row's `batch_group_id` is NULL.
#: Leaving it that way is not a smaller change, it is the end of batching: every
#: Dubai and Northern Emirates order would dispatch on its own, at roughly three
#: times the courier cost, with nothing on any screen to say why. So the
#: membership is restated here rather than inherited.
#:
#: All three Ras al-Khaimah bands join the northern run, because they are one
#: emirate at one fee on one courier and the band that preceded them was already
#: on it. `Dubai Outer` and the `*Inland` bands do not: they are third-party
#: zones and there is no run of ours for them to share.
BATCH_GROUPS: dict[str, tuple[str, ...]] = {
    "Dubai": ("Dubai Near", "Dubai Mid", "Dubai Far"),
    "Northern Emirates": (
        "Sharjah Outer",
        "Ajman City",
        "Umm al-Quwain City",
        "Ras al-Khaimah South",
        "Ras al-Khaimah City",
        "Ras al-Khaimah North",
    ),
}

#: A frozen snapshot, not the file the generator writes.
#:
#: `test_no_migration_reads_the_generators_output` enforces this, and it has
#: caught the same mistake twice: a migration that reads the generator's own
#: output by name stops being able to build an empty database the moment the map
#: is redrawn under it — which is precisely what this change does to `085`.
GEOJSON_PATH = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "data"
    / "uae_delivery_zones.v4.geojson.json"
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
            "Regenerate it with `python -m scripts.build_delivery_zones` and "
            "copy the output to the frozen snapshot this migration names."
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
                "The v1 bands, re-cut so that no zone spans two kinds of run. "
                "Sharjah splits off a 3.5 km Core and an Inland band, leaving "
                "its remainder as the East Coast exclaves; Ajman and Dubai do "
                "the same and leave Masfout and Hatta standing alone; Umm "
                "al-Quwain gains an inland band; Ras al-Khaimah's single 78 km "
                "band becomes South, City and North. Every fee, threshold and "
                "courier is carried through from the zone each band was cut "
                "out of, except Ras al-Khaimah beyond 78 km, which drops from "
                "the third-party 80.00 to the emirate's own 50.00."
            ),
        },
    )

    polygon_ids: dict[str, uuid.UUID] = {}
    for display_order, (name, fee, threshold, provider, free) in enumerate(ZONES):
        geometry = shapes[name]
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        polygon_id = uuid.uuid4()
        polygon_ids[name] = polygon_id
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, delivery_fee, free_delivery_threshold, "
                " pricing_mode, free_delivery_eligible, fulfilment_provider, "
                " alternate_providers, branch_id, "
                " geometry, min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :fee, :threshold, 'static', :free, "
                " :provider, CAST(:alternates AS jsonb), :branch_id, "
                " CAST(:geometry AS jsonb), :min_lat, :max_lat, "
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
                "alternates": ZONE_ALTERNATES.get(name, ALTERNATES[provider]),
                "branch_id": str(branch_id) if branch_id else None,
                "geometry": json.dumps(geometry),
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "display_order": display_order,
            },
        )

    # The schedule, restated. `delivery_batch_windows` hang off the group and
    # not off a polygon since `088`, so there is nothing to copy — only the
    # zone's membership of the group to re-declare.
    for group_name, zone_names in BATCH_GROUPS.items():
        conn.execute(
            sa.text(
                "UPDATE delivery_polygons p SET batch_group_id = g.id "
                "FROM delivery_batch_groups g "
                "WHERE g.name = :group AND p.version_id = :version_id "
                "AND p.name = ANY(:zones)"
            ),
            {
                "group": group_name,
                "version_id": str(version_id),
                "zones": list(zone_names),
            },
        )


def downgrade() -> None:
    """Delete this map and put the previous one back in force.

    One transaction, in the same way and for the same reason as the upgrade: a
    request landing between the delete and the reactivation would find no
    active map at all.
    """
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
