"""Journal automatic daily-sales-report sends in their own table.

The nightly report's idempotency used to be inferred from `email_logs` by a
subject `LIKE '%<date>%'` (F-POS-18): a manual admin send wrote a row with the
date in its subject and silently suppressed that night's automatic send, and a
run where one recipient failed still left a `sent` row and was never retried.

This adds the explicit journal the loop now writes: one row per business date the
nightly report reached EVERY recipient on. Additive — a new table, no change to
`email_logs` — and `IF NOT EXISTS` makes it a no-op on a database that already
carries it.

Revision ID: 206_daily_sales_sends
Revises: 205_forecast_business_date_idx
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "206_daily_sales_sends"
down_revision: Union[str, None] = "205_forecast_business_date_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "daily_sales_sends"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("business_date", sa.String(length=10), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "recipients",
            sa.dialects.postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("business_date"),
        sa.CheckConstraint(
            r"business_date ~ '^\d{4}-\d{2}-\d{2}$'",
            name="ck_daily_sales_sends_business_date_format",
        ),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_table(_TABLE, if_exists=True)
