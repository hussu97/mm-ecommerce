"""Correct the Attibassi Coffee Barsha Heights branch location.

The live ``B001`` branch longitude pointed west of the verified Google Maps
place. This migration records the owner-approved place pin and delivery address
so any environment upgraded from scratch matches production.

Revision ID: 056_correct_barsha_heights_pin
Revises: 055_correct_sharjah_kitchen_pin
Create Date: 2026-08-03
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "056_correct_barsha_heights_pin"
down_revision: Union[str, None] = "055_correct_sharjah_kitchen_pin"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE branches SET address = :address, latitude = :latitude, longitude = :longitude "
            "WHERE reference = :reference AND deleted_at IS NULL"
        ).bindparams(
            reference="B001",
            address="Attibassi Coffee, Al Shafar Tower 1, Barsha Heights, Dubai",
            latitude=25.0984482,
            longitude=55.1741736,
        )
    )


def downgrade() -> None:
    # Keep verified operational coordinates when rolling back schema history;
    # restoring a known-wrong pin would make courier pickup less reliable.
    pass
