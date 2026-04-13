"""Add composite index on regions(is_active, sort_order)

is_active is filtered on every active-region query and every delivery-fee
calculation; sort_order is the ORDER BY column.  Without this index each
query performs a full sequential scan of the regions table.

Uses CREATE INDEX CONCURRENTLY so that live app connections are not blocked
during the build.  CONCURRENTLY cannot run inside a transaction, so this
migration switches the connection to AUTOCOMMIT before executing, which
causes Alembic to commit the version-table update separately afterwards.

Revision ID: 030_regions_is_active_index
Revises: 029_blog_posts
Create Date: 2026-04-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "030_regions_is_active_index"
down_revision: Union[str, None] = "029_blog_posts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CONCURRENTLY avoids a ShareLock that would block reads/writes on regions.
    # It must run outside a transaction — switch to AUTOCOMMIT for this statement.
    connection = op.get_bind()
    connection.execution_options(isolation_level="AUTOCOMMIT")
    connection.execute(
        sa.text(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_regions_active_sort"
            " ON regions (is_active, sort_order)"
        )
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execution_options(isolation_level="AUTOCOMMIT")
    connection.execute(
        sa.text("DROP INDEX CONCURRENTLY IF EXISTS ix_regions_active_sort")
    )
