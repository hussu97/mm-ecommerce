"""Where misc purchase-order spend lands on the P&L: level, channels, branches.

Until now every misc PO line (rent, salaries, cake supplies…) sat below PC3 as
overhead, on the total column only. The shop wants it placed:

* **Level** — PC1 (a cost of the goods, like cake supplies for custom orders),
  PC2, PC3, or PC4 (overhead, the default).
* **Channels** — which sales channels carry it (cake supplies → custom orders).
* **Branches** — which branches carry it (the DSO landlord's rent → DSO). Every
  misc line in production is filed on a Sharjah PO, so the PO's own branch says
  where it was bought, not whom it is for.

A category sets a level and channels; a supplier sets a level, channels and
branches, and wins field by field (`services/orders/misc_expenses`). Empty
arrays mean "all"; a NULL level means "inherit" (category) or PC4.

Pure configuration: nothing is backfilled, and no figure moves until someone
sets one of these in the console.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "308_misc_pnl_allocation"
down_revision: Union[str, None] = "307_drop_dead_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEVELS = "('pc1', 'pc2', 'pc3', 'pc4')"


def upgrade() -> None:
    op.add_column(
        "purchase_order_misc_categories",
        sa.Column("pnl_level", sa.String(3), nullable=True),
    )
    op.add_column(
        "purchase_order_misc_categories",
        sa.Column(
            "pnl_channels",
            postgresql.ARRAY(sa.String(40)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_check_constraint(
        "ck_po_misc_category_pnl_level",
        "purchase_order_misc_categories",
        f"pnl_level IS NULL OR pnl_level IN {_LEVELS}",
    )

    op.add_column("suppliers", sa.Column("misc_pnl_level", sa.String(3), nullable=True))
    op.add_column(
        "suppliers",
        sa.Column(
            "misc_pnl_channels",
            postgresql.ARRAY(sa.String(40)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.add_column(
        "suppliers",
        sa.Column(
            "misc_pnl_branch_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
    )
    op.create_check_constraint(
        "ck_supplier_misc_pnl_level",
        "suppliers",
        f"misc_pnl_level IS NULL OR misc_pnl_level IN {_LEVELS}",
    )


def downgrade() -> None:
    op.drop_constraint("ck_supplier_misc_pnl_level", "suppliers", type_="check")
    op.drop_column("suppliers", "misc_pnl_branch_ids")
    op.drop_column("suppliers", "misc_pnl_channels")
    op.drop_column("suppliers", "misc_pnl_level")
    op.drop_constraint(
        "ck_po_misc_category_pnl_level",
        "purchase_order_misc_categories",
        type_="check",
    )
    op.drop_column("purchase_order_misc_categories", "pnl_channels")
    op.drop_column("purchase_order_misc_categories", "pnl_level")
