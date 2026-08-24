"""Auto-applied promotions, and the standing 15%-off on counter orders.

A promotion could be created and read but never *fired*: the model carried a
trigger and a reward and nothing in the selling path ever looked at it. Two
columns close the gap for the one case the shop actually asked for — a discount
the register puts on by itself:

* `sources` scopes a promotion to the channels that may earn it, using
  `OrderSourceEnum` values. `order_types` already says *dine-in vs delivery*;
  this says *who rang it up*. `["cashier"]` is the counter, and only the
  counter — the storefront (`online`) and the aggregators (`aggregator`) share
  the same `orders` table and must not inherit a discount meant for walk-ins.
* `auto_apply` marks a promotion the pricing engine applies unattended, in
  `pos_order_service.recalculate`, rather than one a cashier picks from a menu.

Then the seed the feature exists for: **every counter order is 15% off.** A
single `auto_apply` promotion, `spend`-triggered at 0 (so it is unconditional),
`percentage_off_order` at 15, scoped to `sources = {cashier}` and every branch
and every order type.

Guarded like every content seed here: it inserts only where no auto-apply
cashier promotion already exists, so once one is present — including after an
admin has edited the 15 down to 12 — this migration matches nothing and changes
nothing, on a fresh database and on one restored from an older dump alike.

Revision ID: 145_counter_auto_discount
Revises: 144_branch_cash_enabled
Create Date: 2026-08-24
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "145_counter_auto_discount"
down_revision: Union[str, None] = "144_branch_cash_enabled"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "promotions",
        sa.Column(
            "sources",
            sa.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.add_column(
        "promotions",
        sa.Column(
            "auto_apply",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )

    # The standing counter discount. `reward_value` is a percent (15 == 15%);
    # the evaluator divides by 100 when it builds the order-level discount.
    op.execute(
        """
        INSERT INTO promotions
            (id, name, name_localized, type, trigger, trigger_value,
             reward, reward_value, sources, auto_apply, priority,
             max_uses_per_order, is_active, created_at, updated_at)
        SELECT gen_random_uuid(), 'Counter 15% Off', 'خصم ١٥٪ على طلبات الكاونتر',
               'basic', 'spend', 0,
               'percentage_off_order', 15, '{cashier}', true, 100,
               1, true, now(), now()
        WHERE NOT EXISTS (
            SELECT 1 FROM promotions
             WHERE auto_apply = true AND 'cashier' = ANY(sources)
        )
        """
    )


def downgrade() -> None:
    # Only the row this migration seeds; an admin-created auto promotion is not
    # ours to remove. Deleted before the columns it needs are dropped.
    op.execute(
        "DELETE FROM promotions WHERE name = 'Counter 15% Off' AND auto_apply = true"
    )
    op.drop_column("promotions", "auto_apply")
    op.drop_column("promotions", "sources")
