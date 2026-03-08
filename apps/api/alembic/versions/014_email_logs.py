"""Add email_logs table for email send history

Revision ID: 014_email_logs
Revises: 013_webhook_events
Create Date: 2026-03-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "014_email_logs"
down_revision: Union[str, None] = "013_webhook_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_logs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("template", sa.String(50), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("order_number", sa.String(30), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("resend_id", sa.String(255), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_email_logs_template", "email_logs", ["template"])
    op.create_index("ix_email_logs_recipient", "email_logs", ["recipient"])
    op.create_index("ix_email_logs_order_number", "email_logs", ["order_number"])
    op.create_index("ix_email_logs_status", "email_logs", ["status"])
    op.create_index("ix_email_logs_sent_at", "email_logs", ["sent_at"])


def downgrade() -> None:
    op.drop_index("ix_email_logs_sent_at", table_name="email_logs")
    op.drop_index("ix_email_logs_status", table_name="email_logs")
    op.drop_index("ix_email_logs_order_number", table_name="email_logs")
    op.drop_index("ix_email_logs_recipient", table_name="email_logs")
    op.drop_index("ix_email_logs_template", table_name="email_logs")
    op.drop_table("email_logs")
