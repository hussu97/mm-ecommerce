"""Make the admin's search boxes stop reading whole tables.

Every search in the console is an unanchored `ILIKE '%term%'` — order number,
email, customer name, customer phone, product name. A btree index cannot help a
pattern that does not start with a literal, so each of those is a sequential
scan over the whole table. That is invisible at today's row counts and gets
slower every month, on the screen an admin uses while a customer is on the phone.

`pg_trgm` GIN indexes are the answer to exactly this shape: they index the
three-character sequences in a string, so a substring match becomes an index
lookup.

The extension is created here rather than assumed. It ships with Postgres and
needs no superuser on a self-hosted instance, which this is.

Concurrent index builds are deliberately *not* used: they cannot run inside a
transaction, and Alembic wraps migrations in one. These tables are small enough
that a plain build is a blip, and a deploy that fails loudly beats one that
half-applies.

Revision ID: 082_search_trigram_indexes
Revises: 081_custom_order_scheduling
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "082_search_trigram_indexes"
down_revision: Union[str, None] = "081_custom_order_scheduling"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (index name, table, column) for every column an admin search box scans.
_TRIGRAM_TARGETS = (
    ("ix_orders_order_number_trgm", "orders", "order_number"),
    ("ix_orders_email_trgm", "orders", "email"),
    ("ix_orders_customer_name_trgm", "orders", "customer_name"),
    ("ix_orders_customer_phone_trgm", "orders", "customer_phone"),
    ("ix_products_name_trgm", "products", "name"),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, table, column in _TRIGRAM_TARGETS:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {name} "
            f"ON {table} USING gin ({column} gin_trgm_ops)"
        )


def downgrade() -> None:
    for name, _table, _column in _TRIGRAM_TARGETS:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    # The extension is left in place: something else may have come to rely on
    # it, and dropping it would take those indexes with it.
