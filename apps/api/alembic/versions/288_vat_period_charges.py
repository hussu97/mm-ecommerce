"""VAT ledger: a category for marketplace charges that belong to no order.

Widens `ck_vat_ledger_entries_category_allowed` with `marketplace_period_charges`,
the input VAT on statement-level marketplace charges: noon's monthly platform and
long-distance fees, Deliveroo's monthly admin fee and its correction credits. The
P&L already reclaimed that VAT (`period_charges`), but the VAT ledger only read
order columns, so it never reached `vat_ledger_entries`.

No data backfill here, for the same reason `260_vat_ledger` has none: the VAT
split lives once, in the async service. On boot the refresh loop sees statement
charges with no ledger row of this category, and rebuilds the full history once
(`vat_ledger._needs_backfill`).

Revision ID: 288_vat_period_charges
Revises: 287_replenishment_forecast
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "288_vat_period_charges"
down_revision: Union[str, None] = "287_replenishment_forecast"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_vat_ledger_entries_category_allowed"

_BEFORE = (
    "sales_output",
    "sales_refund",
    "aggregator_commission",
    "payment_processing",
    "courier_fees",
    "raw_goods",
    "marketplace_marketing",
    "marketplace_cancellation",
)
_AFTER = (*_BEFORE, "marketplace_period_charges")


def _check(categories: tuple[str, ...]) -> str:
    return "category IN (" + ", ".join(f"'{c}'" for c in categories) + ")"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "vat_ledger_entries", type_="check")
    op.create_check_constraint(_CONSTRAINT, "vat_ledger_entries", _check(_AFTER))


def downgrade() -> None:
    # The rows are a cache, rebuilt by the service; drop the ones the narrower
    # CHECK would refuse.
    op.execute(
        "DELETE FROM vat_ledger_entries WHERE category = 'marketplace_period_charges'"
    )
    op.drop_constraint(_CONSTRAINT, "vat_ledger_entries", type_="check")
    op.create_check_constraint(_CONSTRAINT, "vat_ledger_entries", _check(_BEFORE))
