"""Stamp when a transfer's packing list was auto-printed at the source till.

`transfers.auto_printed_at` records the first time the source branch's till
opened on the transfer order's date and printed its packing list, so a later
till opening the same day does not reprint it. Null until then; the manual Print
button never touches it.

Revision ID: 231_transfer_auto_printed_at
Revises: 230_transfer_parent_fanout
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "231_transfer_auto_printed_at"
down_revision: Union[str, None] = "230_transfer_parent_fanout"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transfers",
        sa.Column("auto_printed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transfers", "auto_printed_at")
