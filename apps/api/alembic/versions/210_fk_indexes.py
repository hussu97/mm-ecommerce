"""Index the FK/filter columns the admin and courier-scorecard queries walk.

F-OPS-23. `orders` and `order_items` are the largest tables in the schema, and
each of these columns is now either a join target or a WHERE clause somewhere
that used to mean a sequential scan over both of them:

  * `orders.{device_id,table_id,driver_id,creator_id,closer_id}` — the admin
    courier scorecards and order-detail joins walk these (mm-admin-courier-
    filters); migration 102 deliberately left them unindexed as "audit columns
    ... never the driving side of a hot query" and said outright that any of
    them could be added "the day a real query wants it" — that day is this one.
  * `order_items.{kitchen_flow_id,creator_id,voided_by_id}` — the
    kitchen-display and void-report queries filter on these the same way
    `product_id` already earned its own index in 102.
  * `order_taxes.tax_id` — `uq_order_tax` covers `(order_id, tax_id)` but
    `tax_id` is not its leading column, so a per-tax lookup still scanned.
  * `aggregator_order.mm_order_id` — joins back to `orders` for reconciliation
    and the finance sweep.
  * `ix_orders_status_eq_created`: `orders(status) WHERE status = 'created'` —
    `status` already has a full index (`ix_orders_status`), but `created` is a
    small, hot slice of a table that is mostly historical `delivered`/
    `cancelled` rows; the partial index is small enough to stay warm in cache
    for the queue-style "what's still open" queries, where the full index is
    not. Not named `ix_orders_status_created` — migration 017 already used
    that name for an unrelated `(status, created_at DESC)` index.

Three items on the original audit list turned out to already be covered, and
are deliberately NOT repeated here — `if_not_exists=True` would have silently
no-opped against the same name and made it look like this migration added
them when it added nothing at all:

  * `aggregator_statement_line.mm_order_id` already has `index=True` on the
    model and a real index since migration 151.
  * `order_items.course_id` already has `ix_order_items_course_id` since
    migration 041.
  * `orders(branch_id, business_date, pos_status)` already has
    `ix_orders_branch_business_date_pos_status` since migration 035.

All three were only found by actually running the up/down/up cycle this
change adds to pr-check.yml (F-OPS-29) rather than trusting a static scan of
SQLAlchemy's metadata, which cannot see an index a migration created without
a matching model-level `Index`/`index=True` — see `test_fk_indexes.py`'s
`_RAW_MIGRATION_INDEX` entries for the same class of gap.

Every index here is `CREATE INDEX CONCURRENTLY`: `orders`/`order_items` are
live, high-write tables and a plain `CREATE INDEX` takes a lock that would
block every insert/update for the build's duration. CONCURRENTLY cannot run
inside a transaction block, which is exactly what `env.py` wrapped the whole
`upgrade head` run in until 209_auth_session_revocation's neighbour, migration
030, tried it and reverted — see that file's docstring. `transaction_per_migration
= True` (this repo's env.py, as of the same change that added this migration)
gives each migration its own transaction, and `op.get_context().autocommit_block()`
temporarily suspends *that* transaction for the statements inside it, so this
migration runs standalone without dragging every other one back into the
single-shared-transaction failure mode 030 hit. `if_not_exists=True` /
`if_exists=True` throughout make both directions safe to re-run.

Revision ID: 210_fk_indexes
Revises: 209_auth_session_revocation
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "210_fk_indexes"
down_revision: Union[str, None] = "209_auth_session_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (index name, table, columns)
_SIMPLE_INDEXES: list[tuple[str, str, list[str]]] = [
    ("ix_aggregator_order_mm_order_id", "aggregator_order", ["mm_order_id"]),
    ("ix_orders_device_id", "orders", ["device_id"]),
    ("ix_orders_table_id", "orders", ["table_id"]),
    ("ix_orders_driver_id", "orders", ["driver_id"]),
    ("ix_orders_creator_id", "orders", ["creator_id"]),
    ("ix_orders_closer_id", "orders", ["closer_id"]),
    ("ix_order_items_kitchen_flow_id", "order_items", ["kitchen_flow_id"]),
    ("ix_order_items_creator_id", "order_items", ["creator_id"]),
    ("ix_order_items_voided_by_id", "order_items", ["voided_by_id"]),
    ("ix_order_taxes_tax_id", "order_taxes", ["tax_id"]),
]

# NOT ix_orders_status_created — migration 017 already used that name for an
# unrelated (status, created_at DESC) index.
_PARTIAL_INDEX = "ix_orders_status_eq_created"

# Drop in the reverse order they were created, same convention as every
# other multi-index migration in this tree.
_ALL_INDEX_NAMES = [name for name, _, _ in _SIMPLE_INDEXES] + [_PARTIAL_INDEX]


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for name, table, columns in _SIMPLE_INDEXES:
            op.create_index(
                name,
                table,
                columns,
                unique=False,
                if_not_exists=True,
                postgresql_concurrently=True,
            )

        op.create_index(
            _PARTIAL_INDEX,
            "orders",
            ["status"],
            unique=False,
            if_not_exists=True,
            postgresql_concurrently=True,
            postgresql_where=sa.text("status = 'created'"),
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name in reversed(_ALL_INDEX_NAMES):
            table = "orders"
            for idx_name, idx_table, _ in _SIMPLE_INDEXES:
                if idx_name == name:
                    table = idx_table
                    break
            op.drop_index(
                name,
                table_name=table,
                if_exists=True,
                postgresql_concurrently=True,
            )
