"""One check number per branch per trading day.

`_next_check_number` is `SELECT max(check_number) + 1` with no lock and, until
now, nothing stopping two readers from taking the same answer. Migration 035
created only a non-unique composite index, so the collision was caught — if at
all — by the unique index on the derived `order_number`, which gave one of the
two terminals a 500 on a sale that was otherwise fine.

For a website order attached to a register it was not caught at all:
`attach_online_order` assigns a `check_number` but keeps the storefront's own
`order_number`, so a web order and a counter order could both be "check 12" at
the same counter on the same day — two receipts, two kitchen tickets, one number
called out.

Partial, because the column is nullable and every pre-POS website order has a
NULL in it; a plain unique index would be satisfied by those anyway, but stating
the predicate keeps the index to the rows that mean something.

Existing duplicates are renumbered before the index is built rather than failing
the migration: they are real orders and the number is only a label, so the later
of the two moves to the top of that day's sequence.

Revision ID: 077_check_number_unique
Revises: 076_promo_per_user_limit
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "077_check_number_unique"
down_revision: Union[str, None] = "076_promo_per_user_limit"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Renumber any existing collision, keeping the earliest-opened order on the
    # number it was called out as.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   branch_id,
                   business_date,
                   row_number() OVER (
                       PARTITION BY branch_id, business_date, check_number
                       ORDER BY opened_at, created_at, id
                   ) AS dup_rank
              FROM orders
             WHERE check_number IS NOT NULL
               AND branch_id IS NOT NULL
               AND business_date IS NOT NULL
        ),
        maxima AS (
            SELECT branch_id, business_date, max(check_number) AS top
              FROM orders
             WHERE check_number IS NOT NULL
               AND branch_id IS NOT NULL
               AND business_date IS NOT NULL
             GROUP BY branch_id, business_date
        ),
        renumbered AS (
            SELECT r.id,
                   m.top + row_number() OVER (
                       PARTITION BY r.branch_id, r.business_date ORDER BY r.id
                   ) AS new_number
              FROM ranked r
              JOIN maxima m
                ON m.branch_id = r.branch_id
               AND m.business_date = r.business_date
             WHERE r.dup_rank > 1
        )
        UPDATE orders o
           SET check_number = renumbered.new_number
          FROM renumbered
         WHERE o.id = renumbered.id
        """
    )

    op.create_index(
        "uq_orders_branch_business_date_check_number",
        "orders",
        ["branch_id", "business_date", "check_number"],
        unique=True,
        postgresql_where=(
            "check_number IS NOT NULL "
            "AND branch_id IS NOT NULL "
            "AND business_date IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_orders_branch_business_date_check_number", table_name="orders")
