"""Regions table and delivery_settings table with seed data

Replaces hardcoded region/fee constants in delivery_service.py with
DB-managed configuration. The existing RegionEnum slugs are preserved
as the PKs so no existing address rows need changes.

Revision ID: 027
Revises: 026
Create Date: 2026-04-14
"""

import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "027_regions_delivery_settings"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# ── Seed data ──────────────────────────────────────────────────────────────────

REGIONS_SEED = [
    {
        "slug": "dubai",
        "name_translations": {"en": "Dubai", "ar": "دبي"},
        "delivery_fee": "35.00",
        "is_active": True,
        "sort_order": 1,
    },
    {
        "slug": "sharjah",
        "name_translations": {"en": "Sharjah", "ar": "الشارقة"},
        "delivery_fee": "35.00",
        "is_active": True,
        "sort_order": 2,
    },
    {
        "slug": "ajman",
        "name_translations": {"en": "Ajman", "ar": "عجمان"},
        "delivery_fee": "35.00",
        "is_active": True,
        "sort_order": 3,
    },
    {
        "slug": "abu_dhabi",
        "name_translations": {"en": "Abu Dhabi", "ar": "أبوظبي"},
        "delivery_fee": "50.00",
        "is_active": True,
        "sort_order": 4,
    },
    {
        "slug": "ras_al_khaimah",
        "name_translations": {"en": "Ras Al Khaimah", "ar": "رأس الخيمة"},
        "delivery_fee": "50.00",
        "is_active": True,
        "sort_order": 5,
    },
    {
        "slug": "fujairah",
        "name_translations": {"en": "Fujairah", "ar": "الفجيرة"},
        "delivery_fee": "50.00",
        "is_active": True,
        "sort_order": 6,
    },
    {
        "slug": "umm_al_quwain",
        "name_translations": {"en": "Umm Al Quwain", "ar": "أم القيوين"},
        "delivery_fee": "50.00",
        "is_active": True,
        "sort_order": 7,
    },
    {
        "slug": "al_ain",
        "name_translations": {"en": "Al Ain", "ar": "العين"},
        "delivery_fee": "50.00",
        "is_active": True,
        "sort_order": 8,
    },
    {
        "slug": "rest_of_uae",
        "name_translations": {"en": "Rest of UAE", "ar": "باقي الإمارات"},
        "delivery_fee": "50.00",
        "is_active": True,
        "sort_order": 9,
    },
]


def upgrade() -> None:
    now = datetime.utcnow()

    # ── regions ───────────────────────────────────────────────────────────────
    op.create_table(
        "regions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("slug", sa.String(50), unique=True, nullable=False, index=True),
        sa.Column(
            "name_translations", JSONB, nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "delivery_fee",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0.00",
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    regions_table = sa.table(
        "regions",
        sa.column("id", UUID),
        sa.column("slug", sa.String),
        sa.column("name_translations", JSONB),
        sa.column("delivery_fee", sa.Numeric),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    op.bulk_insert(
        regions_table,
        [
            {
                "id": uuid.uuid4(),
                "slug": r["slug"],
                "name_translations": r["name_translations"],
                "delivery_fee": r["delivery_fee"],
                "is_active": r["is_active"],
                "sort_order": r["sort_order"],
                "created_at": now,
                "updated_at": now,
            }
            for r in REGIONS_SEED
        ],
    )

    # ── delivery_settings (single row) ─────────────────────────────────────────
    op.create_table(
        "delivery_settings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "free_delivery_threshold",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="200.00",
        ),
        sa.Column(
            "pickup_fee",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="0.00",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    settings_table = sa.table(
        "delivery_settings",
        sa.column("id", UUID),
        sa.column("free_delivery_threshold", sa.Numeric),
        sa.column("pickup_fee", sa.Numeric),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    op.bulk_insert(
        settings_table,
        [
            {
                "id": uuid.uuid4(),
                "free_delivery_threshold": "200.00",
                "pickup_fee": "0.00",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("delivery_settings")
    op.drop_table("regions")
