"""Keep every courier push, so a lost one can be told from a missing one.

`webhook_events` records the events we accepted, as an id and nothing else — it
exists to refuse a duplicate, not to explain anything. This table is the other
half: one row per request that arrived, including the ones that matched no
order, presented an unfamiliar key, carried no readable body, or raised inside
the handler.

The morning of 2026-08-05 is the case for it. MM-20260805-007 received four noon
Send pushes and MM-20260805-006 received one, and the only evidence either way
was container stdout, which a restart had already taken. Whether the missing
three were never sent, sent elsewhere, or accepted and dropped is a question
nobody can now answer — and noon Send neither retry nor keep anything to replay
from, so a push we cannot see is a push that never happened.

Rows are purged after `LOG_RETENTION_DAYS` (7). That bound is what makes it
affordable to record rider-position pings at full payload as well: noon Send
send one every 15-30 seconds per live task.

`received_at` is indexed descending because every read is "what just happened",
and the retention sweep walks the other end of the same index.

Revision ID: 074_webhook_logs
Revises: 073_courier_reference
Create Date: 2026-08-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "074_webhook_logs"
down_revision: Union[str, None] = "073_courier_reference"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_logs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("endpoint", sa.String(length=20), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("remote_ip", sa.String(length=60), nullable=True),
        sa.Column("api_key_fingerprint", sa.String(length=60), nullable=True),
        sa.Column("signature_valid", sa.Boolean(), nullable=True),
        sa.Column("event_type", sa.String(length=60), nullable=True),
        sa.Column("order_number", sa.String(length=30), nullable=True),
        sa.Column("courier_order_id", sa.String(length=64), nullable=True),
        sa.Column("matched", sa.Boolean(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
    op.execute(
        "CREATE INDEX ix_webhook_logs_received_at ON webhook_logs (received_at DESC)"
    )
    op.create_index(
        "ix_webhook_logs_provider", "webhook_logs", ["provider", "endpoint"]
    )
    op.create_index("ix_webhook_logs_order_number", "webhook_logs", ["order_number"])
    op.create_index(
        "ix_webhook_logs_courier_order_id", "webhook_logs", ["courier_order_id"]
    )


def downgrade() -> None:
    op.drop_table("webhook_logs")
