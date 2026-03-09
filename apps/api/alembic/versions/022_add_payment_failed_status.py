"""Add payment_failed to orderstatusenum

Revision ID: 022
Revises: 021
Create Date: 2026-03-09
"""

from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE orderstatusenum ADD VALUE 'payment_failed'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    # This downgrade is intentionally a no-op.
    pass
