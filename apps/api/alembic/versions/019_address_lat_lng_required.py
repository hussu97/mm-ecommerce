"""address lat/lng required

Revision ID: 019
Revises: 018
Create Date: 2026-03-09

"""

from alembic import op

revision = "019"
down_revision = "018_address_region_overhaul"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Remove addresses that have no pin — they are now invalid
    op.execute("DELETE FROM addresses WHERE latitude IS NULL OR longitude IS NULL")

    # Make the columns non-nullable
    op.alter_column("addresses", "latitude", nullable=False)
    op.alter_column("addresses", "longitude", nullable=False)


def downgrade() -> None:
    op.alter_column("addresses", "latitude", nullable=True)
    op.alter_column("addresses", "longitude", nullable=True)
