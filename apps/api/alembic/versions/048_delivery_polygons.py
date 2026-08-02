"""Price delivery by where the pin is, not by which emirate someone picked.

The region dropdown was the customer's own guess at their emirate, and it set
the fee. It was wrong in both directions: a Dubai address a few hundred metres
over the Sharjah line, or anyone who left the default alone. Meanwhile we
already had the exact coordinates from the map pin and ignored them.

Zones are now polygons and the fee comes from the point. Because zones get
redrawn — a courier drops an area, a fee moves — the whole set is versioned:
one active map at a time, publishing is a flag, and rolling back is that flag
pointed at the previous one. Editing rows in place would make a bad map
unrecoverable.

Seeds the first version from emirate boundaries (geoBoundaries gbOpen ARE ADM1,
CC-BY 4.0, simplified to roughly 55 m) carrying today's prices exactly:

  * Dubai, Sharjah, Ajman ......... 35 AED
  * Abu Dhabi, Ras al-Khaimah,
    Fujairah, Umm al-Quwain ....... 50 AED

Al Ain needs no polygon of its own: it sits inside Abu Dhabi and charges the
same 50 AED. Anything outside every polygon falls to
`delivery_settings.default_delivery_fee`, which is seeded at 50 to match the
old "Rest of UAE" row.

The `regions` table is left in place. It no longer prices anything, but orders
reference region slugs and the admin still lists them.

Revision ID: 048_delivery_polygons
Revises: 047_address_unit_number
Create Date: 2026-08-02
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "048_delivery_polygons"
down_revision: Union[str, None] = "047_address_unit_number"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Emirate -> (fee, region slug used elsewhere in the app)
ZONES: dict[str, tuple[str, str]] = {
    "Dubai": ("35.00", "dubai"),
    "Sharjah": ("35.00", "sharjah"),
    "Ajman": ("35.00", "ajman"),
    "Abu Dhabi": ("50.00", "abu_dhabi"),
    "Ras al-Khaimah": ("50.00", "ras_al_khaimah"),
    "Fujairah": ("50.00", "fujairah"),
    "Umm al-Quwain": ("50.00", "umm_al_quwain"),
}

GEOJSON_PATH = (
    Path(__file__).resolve().parents[2] / "app" / "data" / "uae_emirates.geojson.json"
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
    op.create_table(
        "delivery_polygon_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    )
    # One live map at a time. A rollback has to deactivate before it activates,
    # which is exactly the guarantee we want.
    op.create_index(
        "uq_delivery_polygon_version_active",
        "delivery_polygon_versions",
        ["is_active"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )

    op.create_table(
        "delivery_polygons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "version_id",
            UUID(as_uuid=True),
            sa.ForeignKey("delivery_polygon_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("region_slug", sa.String(length=30), nullable=True),
        sa.Column("delivery_fee", sa.Numeric(10, 2), nullable=False),
        sa.Column("geometry", JSONB, nullable=False),
        sa.Column("min_lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("max_lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("min_lng", sa.Numeric(9, 6), nullable=False),
        sa.Column("max_lng", sa.Numeric(9, 6), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_delivery_polygons_version_id", "delivery_polygons", ["version_id"]
    )

    op.add_column(
        "delivery_settings",
        sa.Column(
            "default_delivery_fee",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="50.00",
        ),
    )

    # ── Seed the first map ────────────────────────────────────────────────────
    # Failing here fails the deploy, which is the right outcome: an empty map
    # is not a neutral state. Every delivery would silently fall through to the
    # default fee, so a loud failure beats quietly overcharging Dubai.
    if not GEOJSON_PATH.exists():
        raise RuntimeError(
            f"Delivery zone geometry missing at {GEOJSON_PATH}. "
            "The polygon tables were created but no zones would exist, so every "
            "order would be charged the default fee."
        )

    emirates = json.loads(GEOJSON_PATH.read_text())
    version_id = uuid.uuid4()

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "INSERT INTO delivery_polygon_versions (id, name, notes, is_active, activated_at) "
            "VALUES (:id, :name, :notes, true, now())"
        ),
        {
            "id": str(version_id),
            "name": "UAE emirates v1",
            "notes": (
                "Seeded from geoBoundaries gbOpen ARE ADM1 (CC-BY 4.0), simplified to ~55 m. "
                "Carries the fees the region table charged on 2026-08-02."
            ),
        },
    )

    for order, (emirate, (fee, slug)) in enumerate(ZONES.items()):
        geometry = emirates.get(emirate)
        if geometry is None:
            continue
        min_lat, max_lat, min_lng, max_lng = _bbox(geometry)
        conn.execute(
            sa.text(
                "INSERT INTO delivery_polygons "
                "(id, version_id, name, region_slug, delivery_fee, geometry, "
                " min_lat, max_lat, min_lng, max_lng, display_order) "
                "VALUES (:id, :version_id, :name, :slug, :fee, CAST(:geometry AS jsonb), "
                " :min_lat, :max_lat, :min_lng, :max_lng, :display_order)"
            ),
            {
                "id": str(uuid.uuid4()),
                "version_id": str(version_id),
                "name": emirate,
                "slug": slug,
                "fee": fee,
                "geometry": json.dumps(geometry),
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lng": min_lng,
                "max_lng": max_lng,
                "display_order": order,
            },
        )


def downgrade() -> None:
    op.drop_column("delivery_settings", "default_delivery_fee")
    op.drop_index("ix_delivery_polygons_version_id", table_name="delivery_polygons")
    op.drop_table("delivery_polygons")
    op.drop_index(
        "uq_delivery_polygon_version_active", table_name="delivery_polygon_versions"
    )
    op.drop_table("delivery_polygon_versions")
