"""Product merchandising labels: the `labels` array replaces `is_featured`.

A product can fly a corner badge on the storefront ("Website Exclusive", and
later "New"/"Limited"). Modelled like `sales_channels` (migration 045) rather
than a boolean per badge: one `ARRAY(String)` column, guarded by a CHECK that
holds the known set, so a typo in an import is rejected and the next badge is
data rather than another column.

This also **retires `is_featured`**. That boolean was one flag under two names —
the admin called it "Featured", the storefront flew "Bestseller" — and the
featured rail queried it directly. Folding it in as the `bestseller` label gives
one uniform notion of a badge with one place to set it. Every product currently
`is_featured = true` is migrated to carry `bestseller`, then the column and its
two indexes are dropped.

A GIN index on `labels` mirrors the `sales_channels` one so "which products are
bestsellers / website-exclusive" stays an index scan.

Revision ID: 217_product_labels
Revises: 216_report_extra_production_use
Create Date: 2026-09-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "217_product_labels"
down_revision: Union[str, None] = "216_report_extra_production_use"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The known set, spelled out here so the constraint reads the same as
# PRODUCT_LABELS on the model. Keep the two in step.
_LABELS_KNOWN = (
    "labels <@ ARRAY['website_exclusive','bestseller','new','limited']::varchar[]"
)


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "labels",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
    )
    # Nothing outside the set is a label. Without this a stray "exclusive" is a
    # badge the storefront has no styling for and silently drops.
    op.create_check_constraint("ck_products_labels_known", "products", _LABELS_KNOWN)
    op.create_index(
        "ix_products_labels",
        "products",
        ["labels"],
        postgresql_using="gin",
    )

    # Carry the old flag over: a featured product becomes a bestseller-labelled
    # one. This runs before the column is dropped, so nothing is lost.
    op.execute(
        "UPDATE products SET labels = ARRAY['bestseller']::varchar[] "
        "WHERE is_featured = true"
    )

    # The flag and everything indexing it are gone. (Dropping the column would
    # cascade the indexes anyway; naming them keeps the downgrade symmetric.)
    op.execute("DROP INDEX IF EXISTS ix_products_active_featured_order")
    op.execute("DROP INDEX IF EXISTS ix_products_is_featured")
    op.drop_column("products", "is_featured")


def downgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_featured",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.execute(
        "UPDATE products SET is_featured = true WHERE 'bestseller' = ANY(labels)"
    )
    # Restore the two indexes migrations 012 and 017 had put on the column.
    op.create_index("ix_products_is_featured", "products", ["is_featured"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_products_active_featured_order"
        " ON products (is_active, is_featured, display_order)"
    )

    op.drop_index("ix_products_labels", table_name="products")
    op.drop_constraint("ck_products_labels_known", "products")
    op.drop_column("products", "labels")
