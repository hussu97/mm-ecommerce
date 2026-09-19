"""VAT ledger cache: derived per-day VAT figures by legal entity and category.

Creates `vat_ledger_entries`, a cache rebuilt by `app.services.vat_ledger` from
orders, their fee columns, order deliveries and purchase orders — output VAT on
sales vs. input VAT on marketplace fees, payment processing, courier charges and
raw goods, each split net / VAT / gross, per legal entity.

No data backfill here on purpose: the arithmetic that splits VAT-inclusive fees
and resolves a purchase order's branch to a legal entity lives once, in the async
service. The refresh loop runs a full-history backfill the first time it finds the
cache empty, then keeps a trailing window fresh — so the figures are never
computed in two places (a real hazard for a money report). The table starts empty
and fills within the first tick after deploy.

Revision ID: 260_vat_ledger
Revises: 259_drop_supplier_item_cost
Create Date: 2026-09-19
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "260_vat_ledger"
down_revision: Union[str, None] = "259_drop_supplier_item_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CATEGORIES = (
    "sales_output",
    "sales_refund",
    "aggregator_commission",
    "payment_processing",
    "courier_fees",
    "raw_goods",
    "marketplace_marketing",
    "marketplace_cancellation",
)


def upgrade() -> None:
    op.create_table(
        "vat_ledger_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("business_date", sa.String(length=10), nullable=False),
        sa.Column(
            "legal_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("legal_entities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("direction", sa.String(length=6), nullable=False),
        sa.Column("net_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("vat_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("gross_value", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "vat_recoverable", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("recomputed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_date",
            "legal_entity_id",
            "category",
            "direction",
            name="uq_vat_ledger_grain",
        ),
        sa.CheckConstraint(
            "direction IN ('output', 'input')",
            name="ck_vat_ledger_entries_direction_allowed",
        ),
        sa.CheckConstraint(
            "category IN (" + ", ".join(f"'{c}'" for c in _CATEGORIES) + ")",
            name="ck_vat_ledger_entries_category_allowed",
        ),
        sa.CheckConstraint(
            r"business_date ~ '^\d{4}-\d{2}-\d{2}$'",
            name="ck_vat_ledger_entries_business_date_format",
        ),
    )
    op.create_index(
        "ix_vat_ledger_entity_date",
        "vat_ledger_entries",
        ["legal_entity_id", "business_date"],
    )
    op.create_index(
        "ix_vat_ledger_business_date",
        "vat_ledger_entries",
        ["business_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_vat_ledger_business_date", table_name="vat_ledger_entries")
    op.drop_index("ix_vat_ledger_entity_date", table_name="vat_ledger_entries")
    op.drop_table("vat_ledger_entries")
