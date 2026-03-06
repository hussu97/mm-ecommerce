"""Add partial unique index on carts.user_id

Revision ID: 006_cart_user_unique
Revises: 005_promo_discount_check
Create Date: 2026-03-06 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "006_cart_user_unique"
down_revision: Union[str, None] = "005_promo_discount_check"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_carts_user_id ON carts (user_id) WHERE user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("uq_carts_user_id", table_name="carts")
