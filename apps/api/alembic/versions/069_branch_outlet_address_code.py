"""Store the noon Send outlet's address revision alongside its code.

`create-task` accepts an optional `outlet_address_code` — the exact address
revision of the pickup outlet, shaped
`addr::restaurant_outlet::ae::CMFRTF2DXS::2`, where the trailing number is the
revision. The outlet code alone already identifies where a rider collects, so
this is a nicety rather than a requirement; noon's own parameter table does not
even list it.

It lives on the branch for the same reason the outlet code does: a courier
collects from a *place*, and one environment variable cannot hold two answers
once a second kitchen delivers.

Nullable, and staying nullable. A branch without one dispatches exactly as it
does today — the field is simply omitted from the task. That matters more than
it looks: the trailing revision changes whenever noon edits the outlet's
address, so a stale value is worse than an absent one, and "leave it empty"
has to remain a first-class answer.

Revision ID: 069_branch_outlet_address_code
Revises: 068_order_branch_required
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "069_branch_outlet_address_code"
down_revision: Union[str, None] = "068_order_branch_required"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column(
            "noon_send_outlet_address_code", sa.String(length=120), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("branches", "noon_send_outlet_address_code")
