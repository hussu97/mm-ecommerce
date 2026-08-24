"""One list of sales channels per product, replacing two booleans.

`is_web_visible` and `is_pos_visible` only ever meant "which channels sell
this", but as two independent booleans nothing said they were one decision.
They were set in different places, read in different places, and a product
could be edited on one without anyone seeing the other — which is how the
counter ended up offering the nine-piece retail boxes alongside the singles
it actually sells.

`sales_channels` says it once. An empty list is a real state: an item that is
in the catalogue and sold on no channel yet.

The backfill is not a straight translation of the two flags. It also applies
the rule the shop actually trades on:

  * a product already off the website is POS-only and stays that way — that
    is what "POS only" meant;
  * in **Cookies** and **Brownies**, only the singles are rung up at the
    counter. The multipacks ("Fudge Brownies", as against "Fudge Brownie
    Single") are a website line, so they lose the POS channel;
  * everything else sells on both.

Revision ID: 045_product_sales_channels
Revises: 044_menu_group_tree
Create Date: 2026-07-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "045_product_sales_channels"
down_revision: Union[str, None] = "044_menu_group_tree"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The categories where only a "single" is a counter item. Matched on slug so
#: renaming the category in the console does not silently change what the
#: register sells.
COUNTER_SINGLES_ONLY = ("cat-cookies", "cat-brownies")


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "sales_channels",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{pos,web}",
        ),
    )

    op.execute(
        f"""
        UPDATE products p
        SET sales_channels =
            (CASE
               WHEN p.is_pos_visible
                    AND NOT (
                      c.slug IN {COUNTER_SINGLES_ONLY!r}
                      AND p.name NOT ILIKE '%single%'
                    )
               THEN ARRAY['pos']::varchar[] ELSE ARRAY[]::varchar[]
             END)
            ||
            (CASE
               WHEN p.is_web_visible THEN ARRAY['web']::varchar[]
               ELSE ARRAY[]::varchar[]
             END)
        FROM categories c
        WHERE c.id = p.category_id
        """
    )
    # Products with no category at all are not covered by the join above and
    # keep the plain translation of their two flags.
    op.execute(
        """
        UPDATE products p
        SET sales_channels =
            (CASE
               WHEN p.is_pos_visible THEN ARRAY['pos']::varchar[]
               ELSE ARRAY[]::varchar[]
             END)
            ||
            (CASE
               WHEN p.is_web_visible THEN ARRAY['web']::varchar[]
               ELSE ARRAY[]::varchar[]
             END)
        WHERE p.category_id IS NULL
        """
    )

    # Nothing outside this set is a channel. Without the constraint a typo in
    # an import ("website") is a product that quietly sells nowhere.
    op.create_check_constraint(
        "ck_products_sales_channels_known",
        "products",
        "sales_channels <@ ARRAY['pos','web']::varchar[]",
    )
    # Every channel query is `'pos' = ANY(sales_channels)`; GIN is what makes
    # that an index scan rather than a sequential one over the catalogue.
    op.create_index(
        "ix_products_sales_channels",
        "products",
        ["sales_channels"],
        postgresql_using="gin",
    )

    op.drop_index("ix_products_is_pos_visible", table_name="products")
    op.drop_column("products", "is_pos_visible")
    op.drop_column("products", "is_web_visible")


def downgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_web_visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    op.add_column(
        "products",
        sa.Column(
            "is_pos_visible", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
    )
    # And the index dropping the column took with it. `040` drops this by name
    # on its own way down, so without it `alembic downgrade base` stops here.
    op.create_index("ix_products_is_web_visible", "products", ["is_web_visible"])
    op.execute(
        """
        UPDATE products
        SET is_web_visible = 'web' = ANY(sales_channels),
            is_pos_visible = 'pos' = ANY(sales_channels)
        """
    )
    op.create_index(
        "ix_products_is_pos_visible", "products", ["is_pos_visible", "is_active"]
    )
    op.drop_index("ix_products_sales_channels", table_name="products")
    op.drop_constraint("ck_products_sales_channels_known", "products")
    op.drop_column("products", "sales_channels")
