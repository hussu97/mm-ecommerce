"""Add is_active to modifiers

Revision ID: 004_modifier_is_active
Revises: 003_modifiers_replace_variants
Create Date: 2026-03-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004_modifier_is_active"
down_revision: Union[str, None] = "003_modifiers_replace_variants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "modifiers",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.alter_column("modifiers", "is_active", server_default=None)


def downgrade() -> None:
    op.drop_column("modifiers", "is_active")
