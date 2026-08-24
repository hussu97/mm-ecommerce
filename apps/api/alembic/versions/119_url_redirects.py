"""A table of URLs that have moved, and where they went.

Until now the only redirect the shop had was two entries hand-written in
`next.config.ts` for `/about-me`, the Wix site's URL for the about page. That
works exactly once and costs a deploy: the next moved URL is a code change, a
review and a release, so in practice it does not happen and the link stays
broken. Migration 120 is about to move eight category slugs and the 36 product
URLs nested under them, which is not a thing to hard-code.

**Why the paths have no locale.** A slug moves in both languages at once. Storing
`/cat-brownies` rather than `/en/cat-brownies` and `/ar/cat-brownies` means an
operator writes the rule they are thinking about instead of remembering to write
it twice — and the one they forget is the one nobody notices, because whoever
speaks the other language is not the person who made the change. A path that
genuinely only exists in one language can still be written out in full, and the
resolver tries the exact form first.

**Why `is_prefix`.** The category page is the smaller half of what a rename
breaks. `/cat-mixboxes/mix-cookies-box-of-9` is the URL Google actually holds,
and an exact-match rule does not touch it. A prefix rule carries the whole
subtree in one row instead of 36.

**Why no 302.** A row in a table that outlives deploys is not how anybody
expresses "for the next hour", and a temporary redirect nobody remembers to
remove is a URL that never consolidates its ranking. 301 and 308 only; 308 is
the default and 301 is kept for the handful of old clients and link checkers
that only understand that one.

Revision ID: 119_url_redirects
Revises: 118_delivery_copy_matches_map
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "119_url_redirects"
down_revision: Union[str, None] = "118_delivery_copy_matches_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "url_redirects",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("from_path", sa.String(length=500), nullable=False),
        sa.Column("to_path", sa.String(length=500), nullable=False),
        sa.Column(
            "is_prefix", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "status_code", sa.Integer(), nullable=False, server_default=sa.text("308")
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "source", sa.String(length=20), nullable=False, server_default="manual"
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "hit_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Convention 6: internal vocabularies are String/Integer + CHECK, spelled
        # out here and mirrored in `UrlRedirect.__table_args__`. No new PG enum.
        sa.CheckConstraint(
            "status_code IN (301, 308)", name="ck_url_redirects_status_code_allowed"
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'category_rename', 'seed')",
            name="ck_url_redirects_source_allowed",
        ),
        # A row pointing at itself is a loop the browser eventually gives up on.
        # `redirect_service` refuses it too; this catches anything reaching the
        # table another way — a console fix, a restored dump, a later migration.
        sa.CheckConstraint("from_path <> to_path", name="ck_url_redirects_not_self"),
        # Both ends are absolute site paths. Without this, a half-typed
        # "brownies" becomes a redirect to a relative URL that resolves against
        # whichever page the visitor happened to be standing on.
        sa.CheckConstraint(
            "from_path LIKE '/%' AND to_path LIKE '/%'",
            name="ck_url_redirects_absolute_paths",
        ),
    )
    # Unique: one destination per source, so "where does this go" has an answer
    # rather than a race between two rows. It is also the lookup the resolver
    # makes on every miss.
    op.create_index(
        "ix_url_redirects_from_path", "url_redirects", ["from_path"], unique=True
    )
    op.create_index("ix_url_redirects_is_active", "url_redirects", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_url_redirects_is_active", table_name="url_redirects")
    op.drop_index("ix_url_redirects_from_path", table_name="url_redirects")
    op.drop_table("url_redirects")
