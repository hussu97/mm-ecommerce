"""Stop the customer-directory trigger from serialising every order write.

Migration 272 made the directory pull-through with statement triggers on
``orders`` and ``users`` that ran ``UPDATE customer_cache_state SET dirty =
true`` on the one singleton row. A row update holds that row's lock until the
writer commits, so every transaction that wrote any order -- a counter sale,
a register accept, checkout, a marketplace promote -- queued behind whichever
one had touched an order last. A Keeta promote pass held it for minutes;
counter sales waited up to 92s, a register close 141s, and the directory's own
rebuild (taken ``FOR UPDATE`` inside the admin request) 75s.

The trigger now appends to ``customer_cache_changes`` instead. Inserts never
wait on one another, so writers stop contending. The scheduler folds the queue
into ``customer_cache_state.dirty`` -- which only it and migrations write now
-- and rebuilds off the request path (``customer_service.run_forever``).

Deliberately no DDL on ``customer_cache_state``: until this commits, every
order writer still takes that row through the old trigger body, so an ``ALTER``
there would queue behind the longest of them and stall every order write in
the meantime. A new table and a function body swap lock nothing they use.

Revision ID: 317_customer_cache_change_queue
Revises: 316_molten_cookie_own_category
Create Date: 2026-10-31
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "317_customer_cache_change_queue"
down_revision: Union[str, None] = "316_molten_cookie_own_category"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "customer_cache_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    )
    # Same function name, so both existing triggers pick up the new body.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mark_customer_cache_dirty() RETURNS trigger AS $$
        BEGIN
            INSERT INTO customer_cache_changes DEFAULT VALUES;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mark_customer_cache_dirty() RETURNS trigger AS $$
        BEGIN
            UPDATE customer_cache_state SET dirty = true WHERE id IS TRUE;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("UPDATE customer_cache_state SET dirty = true WHERE id IS TRUE")
    op.drop_table("customer_cache_changes")
