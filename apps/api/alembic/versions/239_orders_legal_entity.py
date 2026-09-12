"""Reference the legal entity from every order; backfill history to Fatema.

`235` froze the seller identity as three strings on the order. The entity now
owns that, so the order references `legal_entity_id` instead. Every historical
order is booked under the registered entity (Fatema Cake Sweets) — decided with
the owner — so the VAT-by-entity report has a complete history. Going forward the
resolver stamps the correct entity per (branch, channel).

The VAT *figures* (`vat_rate`/`vat_amount`/`total_excl_vat` and the `order_taxes`
rows) stay — they are the frozen amounts a return reproduces; only the identity
strings move to the FK.

Revision ID: 239_orders_legal_entity
Revises: 238_config_legal_entity
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "239_orders_legal_entity"
down_revision: Union[str, None] = "238_config_legal_entity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "legal_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_entities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_orders_legal_entity_id", "orders", ["legal_entity_id"], if_not_exists=True
    )

    # Every historical order → Fatema (the registered entity). Guarded so a
    # re-run touches nothing already attributed.
    op.execute(
        """
        UPDATE orders
        SET legal_entity_id = (SELECT id FROM legal_entities WHERE reference = 'fatema')
        WHERE legal_entity_id IS NULL
        """
    )

    # The identity now lives on the entity; the frozen VAT numbers stay.
    op.drop_column("orders", "tax_number")
    op.drop_column("orders", "tax_registration_name")
    op.drop_column("orders", "invoice_title")


def downgrade() -> None:
    op.add_column("orders", sa.Column("tax_number", sa.String(50), nullable=True))
    op.add_column(
        "orders", sa.Column("tax_registration_name", sa.String(200), nullable=True)
    )
    op.add_column("orders", sa.Column("invoice_title", sa.String(120), nullable=True))
    op.execute(
        """
        UPDATE orders AS o
        SET tax_number = e.tax_number,
            tax_registration_name = e.legal_name,
            invoice_title = e.invoice_title
        FROM legal_entities AS e
        WHERE o.legal_entity_id = e.id
        """
    )
    op.drop_index("ix_orders_legal_entity_id", table_name="orders", if_exists=True)
    op.drop_column("orders", "legal_entity_id")
