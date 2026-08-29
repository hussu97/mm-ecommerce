"""Confine the standing counter discount to cookies, brownies and cookie melts.

The "Counter 15% Off" auto-apply promotion (seeded in `145`) took 15% off the
*whole* check. The shop only wants it on three categories — Cookies, Brownies
and Cookie Melt — so this adds a `category_ids` scope to promotions and points
the standing one at those three.

* `category_ids` on `promotions` scopes an auto-apply order discount to the
  listed categories; empty keeps the old whole-order behaviour. When set,
  `auto_promotion_service` discounts only the lines whose product is in one of
  the categories, as per-item `OrderDiscount` rows.
* The seed matches the categories **by slug**, not by a hardcoded id, because
  category ids differ per environment (dev/prod). Guarded like every content
  seed here: it fills `category_ids` only where it is still empty on the standing
  cashier promotion, so once an admin edits the set in the console this matches
  nothing and changes nothing — on a fresh database and a restored dump alike.
  If none of the three slugs exist (an unseeded catalogue), it does nothing.

Revision ID: 165_promo_category_scope
Revises: 164_payment_backfill
Create Date: 2026-08-29
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "165_promo_category_scope"
down_revision: Union[str, None] = "164_payment_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "promotions",
        sa.Column(
            "category_ids",
            postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    # Point the standing counter discount at Cookies, Brownies and Cookie Melt.
    # COALESCE guards the NOT NULL column against a NULL array_agg when no slug
    # matches; the `category_ids = '{}'` guard keeps it from overwriting an
    # admin's later edit.
    op.execute(
        """
        UPDATE promotions
           SET category_ids = COALESCE(
                   (SELECT array_agg(id)
                      FROM categories
                     WHERE slug IN ('cookies', 'brownies', 'cookie-melts')),
                   '{}'::uuid[]
               )
         WHERE auto_apply = true
           AND 'cashier' = ANY(sources)
           AND category_ids = '{}'
           AND EXISTS (
                   SELECT 1 FROM categories
                    WHERE slug IN ('cookies', 'brownies', 'cookie-melts')
               )
        """
    )


def downgrade() -> None:
    op.drop_column("promotions", "category_ids")
