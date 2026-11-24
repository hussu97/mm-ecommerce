"""PO misc categories can be kept off the P&L; the VAT category is.

Adds `purchase_order_misc_categories.pnl_excluded` (default false). A category
with it set still files PO lines as before — they count towards the PO's
totals and the supplier's spend — but the profit & loss report leaves them out.

The `VAT` category is the reason: its lines are the quarterly VAT payment to
the FTA. The P&L already accounts for that VAT (net sales exclude output VAT
and input VAT is credited back per level), so showing the payment as a cost
would count it twice.

The content half is guarded so it cannot fight the console: it only flips the
live category named exactly `VAT` (case-insensitive) while it is still false.
Once someone changes it in the admin, this matches nothing.

Revision ID: 333_misc_category_pnl_excluded
Revises: 332_employee_driving_vehicle
Create Date: 2026-11-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "333_misc_category_pnl_excluded"
down_revision: Union[str, None] = "332_employee_driving_vehicle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_order_misc_categories",
        sa.Column(
            "pnl_excluded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        """
        UPDATE purchase_order_misc_categories
           SET pnl_excluded = true, updated_at = now()
         WHERE lower(name) = 'vat'
           AND deleted_at IS NULL
           AND pnl_excluded = false
        """
    )


def downgrade() -> None:
    op.drop_column("purchase_order_misc_categories", "pnl_excluded")
