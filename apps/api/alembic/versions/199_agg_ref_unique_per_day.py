"""An aggregator order ref is unique per DAY, not for all time.

Noon reuses its short order code ("6227") every day, and GrubOps stores it on
``external_reference``. The unique key was
``(source, aggregator_channel, external_reference)`` — global — so a new Noon 6227
collided with an OLD Noon 6227 from a previous day and could not be filed. On
2026-09-06 a live Sharjah Noon order (6227) was lost this way: it adopted a Sept-4
order carrying the same code and never appeared in MM.

The fix, per the shop: the same code may recur across days but must be unique
*within* a day for a channel. Add ``business_date`` to the key. Every aggregator
order already carries a business_date (1,859/1,859) and no
(channel, ref, business_date) triple repeats, so the new index builds clean.

The companion code change scopes the GrubOps adopt lookup to the placed branch and
day so it no longer merges a same-code order from another day in the first place.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "199_agg_ref_unique_per_day"
down_revision: Union[str, None] = "198_promo_owned_agg_money"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "uq_orders_source_channel_external_reference"
_NEW = "uq_orders_agg_channel_ref_business_date"
_WHERE = "(source)::text = 'aggregator'::text"


def upgrade() -> None:
    op.drop_index(_OLD, table_name="orders")
    op.create_index(
        _NEW,
        "orders",
        ["source", "aggregator_channel", "external_reference", "business_date"],
        unique=True,
        postgresql_where=sa.text(_WHERE),
    )


def downgrade() -> None:
    op.drop_index(_NEW, table_name="orders")
    op.create_index(
        _OLD,
        "orders",
        ["source", "aggregator_channel", "external_reference"],
        unique=True,
        postgresql_where=sa.text(_WHERE),
    )
