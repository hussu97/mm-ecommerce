"""Somewhere to keep the registers that want telling about an order.

A website order now lands on a branch's register, but nothing tells the register
it has arrived — it would have to be looking. This is the address book for the
push that does the telling.

Keyed on the token rather than the device, because the token is what Apple
addresses and it is the thing that changes: iOS reissues one on reinstall,
restore, and sometimes for its own reasons. A dead token is revoked rather than
deleted, so a device that has gone quiet stays distinguishable from one that
never registered.

Revision ID: 066_device_push_tokens
Revises: 065_sharjah_central_noon_send
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "066_device_push_tokens"
down_revision: Union[str, None] = "065_sharjah_central_noon_send"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_push_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # Apple has warned for years that 64 hex characters may grow, so this is
        # not sized to today's token.
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column(
            "platform", sa.String(length=10), nullable=False, server_default="apns"
        ),
        sa.Column("bundle_id", sa.String(length=100), nullable=False),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_sandbox", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("token", name="uq_device_push_token"),
    )
    # Every send is "the live tokens for this branch", so that is the index.
    op.create_index(
        "ix_device_push_tokens_branch_id", "device_push_tokens", ["branch_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_device_push_tokens_branch_id", table_name="device_push_tokens")
    op.drop_table("device_push_tokens")
