"""Promotions: an optional usage limit across all branches.

`promotions.usage_limit` caps how many completed orders may carry a promotion,
counted across every branch that runs it. Null = unlimited, which every existing
promotion keeps. The count is not stored. It is read from the orders that carry
the promotion's discount (`auto_promotion_service.usage_counts`), so a void or a
refund can never leave a stale tally behind.

Revision ID: 290_promotion_usage_limit
Revises: 289_talabat_pro_flag
Create Date: 2026-09-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "290_promotion_usage_limit"
down_revision: Union[str, None] = "289_talabat_pro_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("promotions", sa.Column("usage_limit", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_promotions_usage_limit_positive",
        "promotions",
        "usage_limit IS NULL OR usage_limit > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_promotions_usage_limit_positive", "promotions", type_="check"
    )
    op.drop_column("promotions", "usage_limit")
