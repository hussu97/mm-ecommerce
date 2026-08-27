"""Durable aggregator login recipes (credentials + method), sealed at rest.

`aggregator_session` holds the derived cookie/token jar. That dies when
Cloudflare binds a clearance cookie to the laptop that minted it. This table
holds the *login recipe* — which flow to run, and the Fernet-sealed email /
password — so the worker can sign in on the VM instead of shipping a cookie
jar. Selectors and URLs stay in code; this is the kind of flow plus secrets.

Nothing is seeded here. Credentials are written through
`PUT /aggregators/account` (push bearer) so they never land in git.

Revision ID: 154_agg_account
Revises: 153_agg_session_state
Create Date: 2026-08-27
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "154_agg_account"
down_revision: Union[str, None] = "153_agg_session_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CHANNELS = "'careem', 'deliveroo', 'talabat', 'noon', 'keeta'"
_METHODS = "'email_password', 'email_otp', 'email_password_otp', 'sso', 'manual'"


def upgrade() -> None:
    op.create_table(
        "aggregator_account",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("account_ref", sa.String(64), nullable=False, server_default=""),
        sa.Column("login_method", sa.String(32), nullable=False),
        sa.Column("credentials_encrypted", sa.Text(), nullable=True),
        sa.Column("mailbox_encrypted", sa.Text(), nullable=True),
        sa.Column("extras", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        sa.UniqueConstraint("channel", "account_ref", name="uq_aggregator_account"),
        sa.CheckConstraint(
            f"channel IN ({_CHANNELS})", name="ck_aggregator_account_channel"
        ),
        sa.CheckConstraint(
            f"login_method IN ({_METHODS})",
            name="ck_aggregator_account_login_method",
        ),
    )


def downgrade() -> None:
    op.drop_table("aggregator_account")
