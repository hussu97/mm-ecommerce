"""Give shift-report templates a fill order the register enforces at close.

The reports cascade through the ledger — Production & Finished Goods posts the
production that Raw Materials then reports as consumption — so the order they are
filled in is load-bearing, not cosmetic. This adds ``display_order`` (low first)
and seeds the sensible default per kind: production/finished goods first, then raw
materials, then packaging, with spot checks last. Operators can re-order in the
console; this only sets a starting point and never overrides a value already set.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "195_report_template_order"
down_revision: Union[str, None] = "194_fix_inv_line_trigger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ORDER = {
    "production": 1,
    "finished_goods": 1,
    "raw_materials": 2,
    "packaging": 3,
    "spot_check": 9,
}


def upgrade() -> None:
    op.add_column(
        "inventory_report_templates",
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )
    # Seed the default order per kind, but only where it is still the 0 the column
    # was just added with — never stomp an order an operator has already chosen.
    for report_type, order in _ORDER.items():
        op.execute(
            sa.text(
                "UPDATE inventory_report_templates SET display_order = :order "
                "WHERE report_type = :report_type AND display_order = 0"
            ).bindparams(order=order, report_type=report_type)
        )


def downgrade() -> None:
    op.drop_column("inventory_report_templates", "display_order")
