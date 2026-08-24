"""Arabic coupon codes, a discount cap, and a first-N-orders rule.

Three columns on `promo_codes`, plus the index the new rule needs to be
affordable.

`code_ar` is a second name for one coupon, not a second coupon: uniquely indexed
against the same namespace as `code` so the two can never collide, and every
ceiling downstream counts both together.

`max_discount_amount` caps a percentage. 15% of a 60-dirham order is 9 and of a
600-dirham catering order is 90, and only one of those was ever intended.

`first_orders_limit` restricts a code to a customer's first N orders. What makes
it enforceable is the identity it counts over — account, email and phone
together. The account alone is worthless here: guest checkout mints a fresh
`users` row per session, so `max_uses_per_user` has always been trivially
bypassable by not signing in. Phone is the one people reuse.

That count needs two indexes. `orders.customer_phone` had none — it was a
free-text field the counter reads off the screen — and `lower(email)` needs a
functional index because the comparison is case-insensitive.

Revision ID: 086_new_customer_coupons
Revises: 085_cost_banded_map
Create Date: 2026-08-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "086_new_customer_coupons"
down_revision: Union[str, None] = "085_cost_banded_map"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("promo_codes", sa.Column("code_ar", sa.String(50), nullable=True))
    op.create_index("ix_promo_codes_code_ar", "promo_codes", ["code_ar"], unique=True)
    op.add_column(
        "promo_codes",
        sa.Column("max_discount_amount", sa.Numeric(10, 2), nullable=True),
    )
    op.add_column(
        "promo_codes", sa.Column("first_orders_limit", sa.Integer(), nullable=True)
    )

    # `orders.customer_phone` existed for the counter to have somebody to call.
    # It is now read on every coupon validation, which is a checkout-blocking
    # query on a table that only grows.
    op.create_index("ix_orders_customer_phone", "orders", ["customer_phone"])
    op.create_index(
        "ix_orders_email_lower",
        "orders",
        [sa.text("lower(email)")],
    )


def downgrade() -> None:
    op.drop_index("ix_orders_email_lower", table_name="orders")
    op.drop_index("ix_orders_customer_phone", table_name="orders")
    op.drop_column("promo_codes", "first_orders_limit")
    op.drop_column("promo_codes", "max_discount_amount")
    op.drop_index("ix_promo_codes_code_ar", table_name="promo_codes")
    op.drop_column("promo_codes", "code_ar")
