"""Optional aggregator price, and integrator categories live at chosen branches.

Two schema additions behind the marketplace menu:

* ``products.aggregator_price`` and ``modifier_options.aggregator_price`` — the
  price the integrator menu (Foodics' Grubtech price tag, and the portals MM
  writes to directly) quotes **instead of** the counter price. Null, the
  ordinary case, means "sell at the counter price", so nothing moves until a
  figure is set. It sits on options as well as products because a sized item
  (every coffee) carries its whole price on its options and has 0 on the
  product — a marketplace markup there has to be an option price.
* ``menu_groups.marketplace_branch_ids`` — on an integrator category, the
  branches whose marketplace menus carry it. Null, the ordinary case, is every
  branch, which is what every existing category means today. A set list keeps
  the category off every other outlet: inactive at that Foodics branch, never
  created on a non-Foodics outlet's portal, and never pushed to that GrubOps
  location. An array of ids rather than a link table because it is read whole
  and written whole from one picker; the service checks each id is a branch.

``ADD COLUMN`` with no default is a catalog-only change, so it holds its lock
for an instant; ``lock_timeout`` makes a busy table fail the deploy rather than
queue every till read behind it.

Revision ID: 319_aggregator_price_scope
Revises: 318_inventory_costing_settle
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "319_aggregator_price_scope"
down_revision: Union[str, None] = "318_inventory_costing_settle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '10s'")
    op.add_column(
        "products",
        sa.Column("aggregator_price", sa.Numeric(10, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_products_aggregator_price_nonneg",
        "products",
        "aggregator_price IS NULL OR aggregator_price >= 0",
    )
    op.add_column(
        "modifier_options",
        sa.Column("aggregator_price", sa.Numeric(10, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_modifier_options_aggregator_price_nonneg",
        "modifier_options",
        "aggregator_price IS NULL OR aggregator_price >= 0",
    )
    op.add_column(
        "menu_groups",
        sa.Column(
            "marketplace_branch_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=True,
        ),
    )
    # Only an integrator category scopes a marketplace menu; on a branch tree the
    # column would be a second, contradictory answer to "which shop is this".
    op.create_check_constraint(
        "ck_menu_groups_marketplace_scope_integrator",
        "menu_groups",
        "marketplace_branch_ids IS NULL OR "
        "(root_kind = 'integrator' AND parent_id IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_menu_groups_marketplace_scope_integrator", "menu_groups", type_="check"
    )
    op.drop_column("menu_groups", "marketplace_branch_ids")
    op.drop_constraint(
        "ck_modifier_options_aggregator_price_nonneg",
        "modifier_options",
        type_="check",
    )
    op.drop_column("modifier_options", "aggregator_price")
    op.drop_constraint("ck_products_aggregator_price_nonneg", "products", type_="check")
    op.drop_column("products", "aggregator_price")
