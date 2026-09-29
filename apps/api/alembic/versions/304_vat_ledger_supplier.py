"""VAT ledger: purchase rows carry their supplier.

`vat_ledger_entries.supplier_id` (nullable, FK `suppliers` SET NULL, indexed),
set on `raw_goods` rows — a received PO's stock and misc lines alike — so the
input VAT on purchases reads per supplier, and a supplier-level VAT recovery
view needs no further schema. Null on every other category.

The grain unique `uq_vat_ledger_grain` gains the column, `NULLS NOT DISTINCT`
(Postgres 15+), so a null-supplier row is still one per (date, entity,
category, direction) as before.

No data backfill here, for the same reason as `260_vat_ledger`: the split lives
once, in the async service. On boot the refresh loop sees purchase rows with no
supplier and rebuilds the full history once (`vat_ledger._needs_backfill`).

Revision ID: 304_vat_ledger_supplier
Revises: 303_faq_cancel_refund
Create Date: 2026-09-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "304_vat_ledger_supplier"
down_revision: Union[str, None] = "303_faq_cancel_refund"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "vat_ledger_entries"
_GRAIN = "uq_vat_ledger_grain"
_COLUMNS = "business_date, legal_entity_id, category, direction"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("supplier_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_vat_ledger_entries_supplier_id_suppliers",
        _TABLE,
        "suppliers",
        ["supplier_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_vat_ledger_supplier", _TABLE, ["supplier_id"])
    op.drop_constraint(_GRAIN, _TABLE, type_="unique")
    op.execute(
        f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_GRAIN} "
        f"UNIQUE NULLS NOT DISTINCT ({_COLUMNS}, supplier_id)"
    )


def downgrade() -> None:
    # A cache: fold purchases back to one row per grain by dropping the
    # per-supplier rows; the next refresh rebuilds them under the old key.
    op.execute(f"DELETE FROM {_TABLE} WHERE supplier_id IS NOT NULL")
    op.drop_constraint(_GRAIN, _TABLE, type_="unique")
    op.execute(f"ALTER TABLE {_TABLE} ADD CONSTRAINT {_GRAIN} UNIQUE ({_COLUMNS})")
    op.drop_index("ix_vat_ledger_supplier", table_name=_TABLE)
    op.drop_constraint(
        "fk_vat_ledger_entries_supplier_id_suppliers", _TABLE, type_="foreignkey"
    )
    op.drop_column(_TABLE, "supplier_id")
