"""Cap a promo code per customer, and cap a percentage at 100.

Two holes in the same table.

`max_uses` is a campaign-wide ceiling and was the only limit there had ever
been, so one customer could redeem a 500-use code 500 times. `max_uses_per_user`
is the per-person limit that was missing. It is nullable and unset for every
existing row, because that is what those codes have always meant — adding a
limit retroactively would start refusing codes customers are mid-way through
using.

The check constraint is the second half of the percentage ceiling now enforced
in `PromoCodeCreate`. The column only ever had `discount_value > 0`, so a 150%
code was writable: it made the discount larger than the basket, which made the
subtotal negative, which made the VAT negative, and
`payment_service.create_session` reads a total at or below zero as "nothing to
charge" and confirms the order for free.

Existing rows are clamped rather than the migration failing on them, since a
percentage above 100 is not a value anyone can have meant.

Revision ID: 076_promo_per_user_limit
Revises: 075_order_promised_at
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "076_promo_per_user_limit"
down_revision: Union[str, None] = "075_order_promised_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "promo_codes",
        sa.Column("max_uses_per_user", sa.Integer(), nullable=True),
    )

    op.execute(
        """
        UPDATE promo_codes
           SET discount_value = 100
         WHERE discount_type = 'percentage'
           AND discount_value > 100
        """
    )
    op.create_check_constraint(
        "ck_promo_percentage_ceiling",
        "promo_codes",
        "discount_type <> 'percentage' OR discount_value <= 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_promo_percentage_ceiling", "promo_codes", type_="check")
    op.drop_column("promo_codes", "max_uses_per_user")
