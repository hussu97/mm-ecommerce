"""Address region overhaul: replace emirate enum + city with region varchar + lat/lng

Revision ID: 018_address_region_overhaul
Revises: 017_cart_and_order_indexes
Create Date: 2026-03-08 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "018_address_region_overhaul"
down_revision = "017_cart_and_order_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop city column
    op.drop_column("addresses", "city")

    # 2. Add region as nullable VARCHAR first (so we can populate it)
    op.add_column("addresses", sa.Column("region", sa.String(30), nullable=True))

    # 3. Populate region from existing emirate column
    #    LOWER(REPLACE(emirate::text, ' ', '_')) maps all 7 existing values correctly:
    #    'Dubai' -> 'dubai', 'Abu Dhabi' -> 'abu_dhabi', 'Ras Al Khaimah' -> 'ras_al_khaimah', etc.
    op.execute("UPDATE addresses SET region = LOWER(REPLACE(emirate::text, ' ', '_'))")

    # 4. Make region NOT NULL now that it's populated
    op.alter_column("addresses", "region", nullable=False)

    # 5. Drop the old emirate column
    op.drop_column("addresses", "emirate")

    # 6. Drop the old enum type
    op.execute("DROP TYPE IF EXISTS emirateenum")

    # 7. Add latitude and longitude columns
    op.add_column("addresses", sa.Column("latitude", sa.Numeric(9, 6), nullable=True))
    op.add_column("addresses", sa.Column("longitude", sa.Numeric(9, 6), nullable=True))


def downgrade() -> None:
    # Re-create the emirateenum type
    op.execute(
        """
        CREATE TYPE emirateenum AS ENUM (
            'Dubai', 'Sharjah', 'Ajman', 'Abu Dhabi',
            'Ras Al Khaimah', 'Fujairah', 'Umm Al Quwain'
        )
        """
    )

    # Drop lat/lng
    op.drop_column("addresses", "longitude")
    op.drop_column("addresses", "latitude")

    # Re-add emirate column (nullable initially)
    op.add_column(
        "addresses",
        sa.Column(
            "emirate",
            sa.Enum(
                "Dubai",
                "Sharjah",
                "Ajman",
                "Abu Dhabi",
                "Ras Al Khaimah",
                "Fujairah",
                "Umm Al Quwain",
                name="emirateenum",
            ),
            nullable=True,
        ),
    )

    # Map region codes back to emirate enum values (best-effort; al_ain and rest_of_uae fall back to Abu Dhabi)
    op.execute(
        """
        UPDATE addresses SET emirate = CASE region
            WHEN 'dubai'          THEN 'Dubai'
            WHEN 'sharjah'        THEN 'Sharjah'
            WHEN 'ajman'          THEN 'Ajman'
            WHEN 'abu_dhabi'      THEN 'Abu Dhabi'
            WHEN 'ras_al_khaimah' THEN 'Ras Al Khaimah'
            WHEN 'fujairah'       THEN 'Fujairah'
            WHEN 'umm_al_quwain'  THEN 'Umm Al Quwain'
            ELSE 'Dubai'
        END::emirateenum
        """
    )

    op.alter_column("addresses", "emirate", nullable=False)
    op.drop_column("addresses", "region")

    # Re-add city column
    op.add_column("addresses", sa.Column("city", sa.String(100), nullable=True))
    op.execute("UPDATE addresses SET city = ''")
    op.alter_column("addresses", "city", nullable=False)
