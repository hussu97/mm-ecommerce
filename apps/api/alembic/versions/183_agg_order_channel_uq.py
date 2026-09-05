"""Key the aggregator-order idempotency backstop on the channel too.

`uq_orders_source_external_reference` (migration 132) enforced one aggregator MM
order per `external_reference`. For a GrubOps order that `external_reference` is
GrubTech's SHORT `externalId` (e.g. "6600") — a per-branch-per-day sequence that
DIFFERENT channels reuse. So a Noon order and a Deliveroo order that both carried
"6600" could not coexist: the unique key forced the second to be adopted onto the
first, collapsing two real orders into one MM order (found once in prod:
AGG-20260901-045, a Noon + Deliveroo "6600" merge).

The paired code change channel-scopes the GrubOps order-adopt so it no longer
merges across channels; this widens the backstop to `(source, aggregator_channel,
external_reference)` so the two orders can each exist. Adding a column to a unique
key only ever PERMITS more rows, so no existing row can violate the new index —
the old key was the stricter superset.

Idempotency is preserved: the same GrubOps order always re-ingests under the same
channel spelling and external_reference, so a re-run still collapses to one row;
only two genuinely different channels sharing a short code are now allowed apart.

Revision ID: 183_agg_order_channel_uq
Revises: 182_branch_hours_sync_run
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "183_agg_order_channel_uq"
down_revision: Union[str, None] = "182_branch_hours_sync_run"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = "uq_orders_source_external_reference"
_NEW = "uq_orders_source_channel_external_reference"
_WHERE = sa.text("source = 'aggregator'")


def upgrade() -> None:
    # Create the wider key first, then drop the old one — never leave the table
    # without an idempotency backstop mid-migration.
    op.create_index(
        _NEW,
        "orders",
        ["source", "aggregator_channel", "external_reference"],
        unique=True,
        postgresql_where=_WHERE,
    )
    op.drop_index(_OLD, table_name="orders")


def downgrade() -> None:
    op.create_index(
        _OLD,
        "orders",
        ["source", "external_reference"],
        unique=True,
        postgresql_where=_WHERE,
    )
    op.drop_index(_NEW, table_name="orders")
