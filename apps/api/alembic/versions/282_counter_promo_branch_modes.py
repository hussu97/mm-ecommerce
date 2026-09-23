"""Counter promotions run per branch, as auto or as a till coupon.

A counter promotion used to be global: `auto_apply` on, and it discounted every
qualifying check at every branch. The shop now wants each branch to run a
promotion either automatically or as a one-tap coupon the cashier chooses.

* `promotions.auto_branch_ids` / `coupon_branch_ids` — explicit branch lists
  (empty means *nowhere*, unlike `branch_ids`), disjoint by CHECK. The engine
  reads these; `auto_apply` stays as a compatibility column the API writes as
  `cardinality(auto_branch_ids) > 0`.
* Backfill keeps today's behaviour: every `auto_apply` promotion becomes auto at
  the branches in its `branch_ids`, or — when that is empty, "every branch" — at
  every live branch that runs the register (`uses_pos`). Guarded on
  `auto_branch_ids = '{}'`, so a re-run (or a console edit made since) is left
  alone.
* `orders.applied_coupon_promotion_id` — the coupon the cashier tapped.
* The `pos.promotions.apply` permission, granted to the till roles
  (`Cashier Staff`, `Manager`) by name, idempotently. A database without those
  roles is untouched.

Revision ID: 282_counter_promo_branch_modes
Revises: 281_fifo_costing_v3
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "282_counter_promo_branch_modes"
down_revision: Union[str, None] = "281_fifo_costing_v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PERMISSION = "pos.promotions.apply"
_ROLES = ("Cashier Staff", "Manager")


def upgrade() -> None:
    for column in ("auto_branch_ids", "coupon_branch_ids"):
        op.add_column(
            "promotions",
            sa.Column(
                column,
                postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    op.create_check_constraint(
        "ck_promotions_branch_modes_disjoint",
        "promotions",
        "NOT (auto_branch_ids && coupon_branch_ids)",
    )

    # Today's behaviour, made explicit. COALESCE guards the NOT NULL column
    # against a NULL array_agg when no branch runs the register.
    op.execute(
        """
        UPDATE promotions
           SET auto_branch_ids = CASE
                   WHEN cardinality(branch_ids) > 0 THEN branch_ids
                   ELSE COALESCE(
                       (SELECT array_agg(b.id ORDER BY b.id)
                          FROM branches b
                         WHERE b.uses_pos = true
                           AND b.deleted_at IS NULL),
                       '{}'::uuid[]
                   )
               END
         WHERE auto_apply = true
           AND auto_branch_ids = '{}'
        """
    )
    # Keep the compatibility column honest: a promotion that resolved to no
    # branch at all is no longer auto anywhere.
    op.execute(
        """
        UPDATE promotions
           SET auto_apply = (cardinality(auto_branch_ids) > 0)
         WHERE auto_apply IS DISTINCT FROM (cardinality(auto_branch_ids) > 0)
        """
    )

    op.add_column(
        "orders",
        sa.Column(
            "applied_coupon_promotion_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "promotions.id",
                name="fk_orders_applied_coupon_promotion_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )

    # Literals rather than bound params: asyncpg types every parameter strictly
    # and `roles.permissions` is varchar[] (see migration 261's grant).
    op.execute(
        f"""
        UPDATE roles
           SET permissions = array_append(permissions, '{_PERMISSION}')
         WHERE name IN ({", ".join(f"'{r}'" for r in _ROLES)})
           AND NOT (permissions @> ARRAY['{_PERMISSION}']::varchar[])
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE roles
           SET permissions = array_remove(permissions, '{_PERMISSION}')
         WHERE permissions @> ARRAY['{_PERMISSION}']::varchar[]
        """
    )
    op.drop_constraint(
        "fk_orders_applied_coupon_promotion_id", "orders", type_="foreignkey"
    )
    op.drop_column("orders", "applied_coupon_promotion_id")
    op.drop_constraint(
        "ck_promotions_branch_modes_disjoint", "promotions", type_="check"
    )
    op.drop_column("promotions", "coupon_branch_ids")
    op.drop_column("promotions", "auto_branch_ids")
