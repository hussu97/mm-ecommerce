"""Give an address somewhere to put the flat, office or floor.

A pin plus a street line gets a rider to the building. It does not get the box
to the door. Until now the only place for "Flat 1203" was `address_line_2`,
which is also where the geocoder's own second line wants to go, so the two
fought over one field and the unit was the one that lost.

`unit_number` is its own column: short, optional, and the only part of the
address the map can never fill in for the customer.

Revision ID: 047_address_unit_number
Revises: 046_mix_box_choice_rules
Create Date: 2026-08-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "047_address_unit_number"
down_revision: Union[str, None] = "046_mix_box_choice_rules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "addresses",
        sa.Column("unit_number", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("addresses", "unit_number")
