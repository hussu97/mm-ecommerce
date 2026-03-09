"""Drop first_name and last_name from users table

Revision ID: 021
Revises: 020
Create Date: 2026-03-09
"""

from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("users", "first_name")
    op.drop_column("users", "last_name")


def downgrade() -> None:
    import sqlalchemy as sa

    op.add_column(
        "users",
        sa.Column("last_name", sa.String(100), nullable=False, server_default=""),
    )
    op.add_column(
        "users",
        sa.Column("first_name", sa.String(100), nullable=False, server_default=""),
    )
