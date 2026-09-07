"""Stamp a revision counter on each delivery map version (F-COU-9).

The active map's parsed zones are cached in-process, per worker, keyed by the
version id. Editing a live zone's fee or courier in place does not mint a new
version, so that key never moved: only the worker that served the edit cleared
its cache, and every other worker kept quoting the old value until it restarted.

This adds a `revision` counter the edit path bumps in the same transaction as
the change. The cache key becomes `(version id, revision)`, so once the edit
commits, every worker's next read computes a new key, misses, and re-reads the
new map — no cross-worker cache-busting message required.

Additive and safe: a NOT NULL integer with a server default of 0, so existing
rows backfill to 0 without a table rewrite and no code has to supply it.

Revision ID: 215_delivery_version_revision
Revises: 209_auth_session_revocation
Create Date: 2026-09-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "215_delivery_version_revision"
down_revision: Union[str, None] = "209_auth_session_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "delivery_polygon_versions"
_COLUMN = "revision"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COLUMN,
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, _COLUMN)
