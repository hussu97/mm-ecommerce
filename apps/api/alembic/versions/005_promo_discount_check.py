"""Add discount_value > 0 check constraint to promo_codes

Revision ID: 005_promo_discount_check
Revises: 004_modifier_is_active
Create Date: 2026-03-06 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "005_promo_discount_check"
down_revision: Union[str, None] = "004_modifier_is_active"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_check_constraint(
        "ck_promo_discount_positive",
        "promo_codes",
        "discount_value > 0",
    )


def downgrade() -> None:
    op.drop_constraint("ck_promo_discount_positive", "promo_codes", type_="check")
