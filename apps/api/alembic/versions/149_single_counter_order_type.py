"""Collapse counter orders to a single `pickup` order type.

The register no longer offers an order-type choice (Dine In / Takeaway /
Delivery / Drive Thru) — a counter order is always `pickup`. `dine_in` and
`drive_thru` are retired everywhere; `pickup` and `delivery` survive because
online and aggregator orders still set `order_type` from `delivery_method`.

This carries three things the deploy needs:

* **Order-type backfill.** Every counter order (`source = 'cashier'`) becomes
  `pickup`, including the handful ever rung up as a counter delivery — the shop
  asked for one counter type, full stop. Any stray `dine_in`/`drive_thru` from
  any source is coerced too, so no row is left holding a value the new CHECK
  forbids. Guarded like every content migration here: it matches only cashier
  rows and the two retired values, so a re-run — or a run against a restored
  dump — changes nothing once the estate is already `pickup`.
* **CHECK.** `order_type` is held to `pickup`/`delivery` (or NULL, which a pure
  web order carries until a register attaches it), mirroring
  `Order.__table_args__` and the `OrderTypeEnum` it is derived from.
* **Collected backfill.** Every settled counter sale still sitting at `confirmed`
  is moved to `delivered` (rendered "Collected"), matching the new behaviour
  where closing a check collects it. A `backfill` timeline row is written at the
  check's `closed_at`; both steps are guarded so a re-run is a no-op.

Revision ID: 149_single_counter_order_type
Revises: 148_payment_failure_reason
Create Date: 2026-08-25
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "149_single_counter_order_type"
down_revision: Union[str, None] = "148_payment_failure_reason"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_orders_order_type_allowed"


def upgrade() -> None:
    # Backfill before the CHECK, or the constraint would reject the very rows it
    # is meant to describe.
    op.execute("UPDATE orders SET order_type = 'pickup' WHERE source = 'cashier'")
    op.execute(
        "UPDATE orders SET order_type = 'pickup' "
        "WHERE order_type IN ('dine_in', 'drive_thru')"
    )
    op.create_check_constraint(
        _CONSTRAINT,
        "orders",
        "order_type IS NULL OR order_type IN ('pickup', 'delivery')",
    )

    # Collect the history too. A counter sale now reaches `delivered` (rendered
    # "Collected") the moment the till closes it; before this, every past
    # cashier sale stopped at `confirmed` and never showed in a fulfilment or
    # collected view. Bring them into line: a settled counter check
    # (`source = cashier`, `pos_status = closed`) sitting at `confirmed` becomes
    # `delivered`.
    #
    # Raw SQL rather than `order_lifecycle.transition()` because this is a
    # one-time historical reconstruction, not a live move: `confirmed → delivered`
    # is a valid transition, a cashier `delivered` fires no consequences
    # (no courier, no refund, no Foodics mirror — all gated to online/aggregator),
    # and the timeline row the ORM listener would have written is written here by
    # hand, stamped `backfill` at the check's own `closed_at` so it reads as the
    # reconstruction it is and not a contemporaneous record. Exactly the shape of
    # migration 091.
    #
    # Guarded twice over: the event insert skips any order that already has a
    # `delivered` row, and the status update matches only `confirmed`, so once a
    # sale is collected this migration — re-run, or run against an older dump —
    # touches nothing.
    op.execute(
        """
        INSERT INTO order_status_events
            (id, order_id, status, previous_status, at, source, note)
        SELECT gen_random_uuid(), o.id, 'delivered', 'confirmed',
               COALESCE(o.closed_at, o.updated_at, now()), 'backfill',
               'counter sale collected at close (backfill)'
        FROM orders o
        WHERE o.source = 'cashier'
          AND o.status = 'confirmed'
          AND o.pos_status = 'closed'
          AND NOT EXISTS (
              SELECT 1 FROM order_status_events e
               WHERE e.order_id = o.id AND e.status = 'delivered'
          )
        """
    )
    op.execute(
        """
        UPDATE orders SET status = 'delivered'
        WHERE source = 'cashier' AND status = 'confirmed' AND pos_status = 'closed'
        """
    )


def downgrade() -> None:
    # The collapsed history is not recoverable and is not meant to be — only the
    # CHECK is reversible.
    op.drop_constraint(_CONSTRAINT, "orders", type_="check")
