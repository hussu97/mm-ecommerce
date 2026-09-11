"""Branch flags for the WMS: uses_pos and a return-branch mapping.

- ``uses_pos`` (default true): whether the branch runs the POS register. A branch
  that does not (DSO, Karama) has no till to receive a transfer/return, so the
  system auto-completes the receive leg for it. Every existing branch runs the
  POS, hence the default.
- ``return_branch_id`` (self-FK, nullable): the central branch this one sends
  returns to (e.g. Sharjah). Null until an admin sets it.

Revision ID: 223_branch_pos_and_return
Revises: 222_transfer_templates_and_kind
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "223_branch_pos_and_return"
down_revision: Union[str, None] = "222_transfer_templates_and_kind"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "branches",
        sa.Column("uses_pos", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "branches",
        sa.Column(
            "return_branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("branches", "return_branch_id")
    op.drop_column("branches", "uses_pos")
