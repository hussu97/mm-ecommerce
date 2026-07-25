"""Add composite index on regions(is_active, sort_order)

is_active is filtered on every active-region query and every delivery-fee
calculation; sort_order is the ORDER BY column.  Without this index each
query performs a full sequential scan of the regions table.

This originally used CREATE INDEX CONCURRENTLY to avoid locking the table
while the index built. That needs the statement to run outside a transaction,
and the contortions to arrange it — commit Alembic's transaction, switch the
connection to AUTOCOMMIT — quietly broke the migration run as a whole: the
committed outer transaction meant every later migration reported success and
then vanished on exit, and `alembic upgrade head` could not build an empty
database at all.

`regions` holds nine rows. The index builds in well under a millisecond and
the lock is not observable, so the plain form is both correct and faster to
reason about. Reach for CONCURRENTLY on a table where the lock would actually
be felt, and give it a migration of its own.

Revision ID: 030_regions_is_active_index
Revises: 029_blog_posts
Create Date: 2026-04-14
"""

from typing import Sequence, Union

from alembic import op

revision: str = "030_regions_is_active_index"
down_revision: Union[str, None] = "029_blog_posts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_regions_active_sort",
        "regions",
        ["is_active", "sort_order"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_regions_active_sort", table_name="regions", if_exists=True)
