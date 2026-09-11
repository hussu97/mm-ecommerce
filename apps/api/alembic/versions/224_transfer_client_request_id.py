"""Idempotency token on transfer orders (POS create+send).

A till that loses the response to a create+send and retries would otherwise ship
the same box twice (a fresh order → a fresh TRANSFER_SEND → the source decremented
again). The client now sends a stable ``client_request_id``; a second create with
the same token is refused by a partial unique index and the service returns the
order it already made.

Revision ID: 224_transfer_client_request_id
Revises: 223_branch_pos_and_return
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "224_transfer_client_request_id"
down_revision: Union[str, None] = "223_branch_pos_and_return"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transfer_orders",
        sa.Column("client_request_id", sa.String(64), nullable=True),
    )
    op.create_index(
        "uq_transfer_orders_client_request_id",
        "transfer_orders",
        ["client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_transfer_orders_client_request_id", table_name="transfer_orders")
    op.drop_column("transfer_orders", "client_request_id")
