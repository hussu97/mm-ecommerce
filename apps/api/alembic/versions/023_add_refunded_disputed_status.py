"""Add refunded and disputed to orderstatusenum

Revision ID: 023
Revises: 022
Create Date: 2026-04-13
"""

from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE orderstatusenum ADD VALUE 'refunded'")
    op.execute("ALTER TYPE orderstatusenum ADD VALUE 'disputed'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without recreating the type.
    # This downgrade is intentionally a no-op.
    pass
