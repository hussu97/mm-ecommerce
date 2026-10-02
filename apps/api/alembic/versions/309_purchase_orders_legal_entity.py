"""Book every purchase order under a legal entity; backfill from its branch.

A PO carried only a branch, and each reader worked the buying entity out for
itself: the VAT ledger and the P&L's misc reclaim from the branch's counter tax
config, and the P&L's misc split not at all (a misc cost landed on any entity's
sales). The PO now references ``legal_entity_id``, frozen at creation from the
same ``branch_channel_tax_configs`` counter row that decides a branch's counter
VAT (Sharjah, Karama, DSO → Fatema Cake Sweets; Barsha → Najm AlShamal, which is
not VAT-registered), falling back to Fatema like an order does.

Backfill: every PO from that rule. A PO under an entity that is not
VAT-registered reclaims nothing, so its lines are re-split with no VAT slice
(net = gross) and its totals refrozen — the gross, and so the stock cost, does
not move. Production has no such PO (every PO is Sharjah's); this keeps any
other environment consistent with what the write paths now do.

Revision ID: 309_purchase_orders_legal_entity
Revises: 308_misc_pnl_allocation
Create Date: 2026-10-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "309_purchase_orders_legal_entity"
down_revision: Union[str, None] = "308_misc_pnl_allocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_orders",
        sa.Column(
            "legal_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_entities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_purchase_orders_legal_entity_id",
        "purchase_orders",
        ["legal_entity_id"],
        if_not_exists=True,
    )

    # The branch's active counter config names the entity; no row → Fatema.
    op.execute(
        """
        UPDATE purchase_orders AS po
        SET legal_entity_id = COALESCE(
            (
                SELECT c.legal_entity_id
                FROM branch_channel_tax_configs AS c
                WHERE c.branch_id = po.branch_id
                  AND c.channel_class = 'counter'
                  AND c.is_active
            ),
            (SELECT id FROM legal_entities WHERE reference = 'fatema')
        )
        WHERE po.legal_entity_id IS NULL
        """
    )

    # An unregistered entity reclaims no input VAT: drop the slice from its
    # lines and refreeze the PO's totals from them (gross is untouched).
    for table in ("purchase_order_items", "purchase_order_misc_items"):
        op.execute(
            f"""
            UPDATE {table} AS line
            SET vat_amount = 0, net_total = line.entered_total
            FROM purchase_orders AS po
            JOIN legal_entities AS e ON e.id = po.legal_entity_id
            WHERE line.purchase_order_id = po.id
              AND NOT e.vat_registered
              AND line.vat_amount <> 0
            """
        )
    op.execute(
        """
        UPDATE purchase_orders AS po
        SET vat_total = 0, subtotal_net = po.total_gross
        FROM legal_entities AS e
        WHERE e.id = po.legal_entity_id
          AND NOT e.vat_registered
          AND po.vat_total <> 0
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_purchase_orders_legal_entity_id",
        table_name="purchase_orders",
        if_exists=True,
    )
    op.drop_column("purchase_orders", "legal_entity_id")
