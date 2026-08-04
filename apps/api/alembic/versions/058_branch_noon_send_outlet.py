"""Move the noon Send outlet code onto the branch that owns it.

A courier collects from a place, and every other fact about that place — where
it is, what its phone number is, whether it takes online orders at all — already
lives on `branches`. The noon Send outlet code was the exception, sitting in the
environment as `NOON_SEND_OUTLET_CODE`, which quietly assumes there will only
ever be one place we dispatch from.

That assumption has a date on it. The moment Barsha Heights starts delivering,
there are two kitchens, each registered separately with noon Send, each with its
own code, each serving its own area — and one environment variable cannot hold
two answers.

So the column moves here. The setting survives as a fallback for a branch that
has no code of its own, which is what keeps the current deployment working
without anyone touching the admin.

This is additive and nullable: a branch without a code simply does not dispatch
through noon Send, which is the behaviour every branch has today.

Revision ID: 058_branch_noon_send_outlet
Revises: 057_noon_send_zone
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "058_branch_noon_send_outlet"
down_revision: Union[str, None] = "057_noon_send_zone"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column("noon_send_outlet_code", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("branches", "noon_send_outlet_code")
