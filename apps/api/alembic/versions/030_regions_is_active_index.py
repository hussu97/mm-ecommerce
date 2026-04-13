"""Add composite index on regions(is_active, sort_order)

is_active is filtered on every active-region query and every delivery-fee
calculation; sort_order is the ORDER BY column.  Without this index each
query performs a full sequential scan of the regions table.

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
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_regions_active_sort"
        " ON regions (is_active, sort_order)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_regions_active_sort")
