"""Index closed orders by (branch_id, business_date) for the sales forecast.

`sales_predictions` groups closed orders by weekday over a bounded window of
recent trading (F-POS-17). Before the window bound it read the whole table; with
the bound it still needed an index to avoid a sequential scan of every order on
each request. This is the partial index the query is written against:
`(branch_id, business_date) WHERE pos_status = 'closed'` — narrow, because only
closed orders feed the forecast, and it doubles as a covering index for the
branch-scoped variant.

Additive and safe to build online: it creates no constraint and touches no data,
and `IF NOT EXISTS` makes it a no-op on a database that already carries it (a
restored dump, a re-run).

Revision ID: 205_forecast_business_date_idx
Revises: 204_agg_subtotal_inclusive
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "205_forecast_business_date_idx"
down_revision: Union[str, None] = "204_agg_subtotal_inclusive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX = "ix_orders_branch_business_date_closed"


def upgrade() -> None:
    op.create_index(
        _INDEX,
        "orders",
        ["branch_id", "business_date"],
        postgresql_where=sa.text("pos_status = 'closed'"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="orders", if_exists=True)
