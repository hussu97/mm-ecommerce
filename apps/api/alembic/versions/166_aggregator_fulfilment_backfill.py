"""Backfill aggregator riders into the shared fulfilment tables.

Aggregator orders kept their rider on `orders.aggregator_driver_*` while online
orders use `order_deliveries` + `order_drivers`. Promotion now mirrors the rider
into those shared tables (services/aggregators/aggregator_fulfilment.py) so the
order-details page shows ONE fulfilment section for every order type — but that
only fires on the next promote of a CHANGED order, so the ~1.2k existing
aggregator orders need a one-off backfill (CLAUDE.md §7).

The rows created are deliberately INERT to the dispatch/batching/tracking
machinery: no courier_order_id, batch_id, dispatchable_at, next_attempt_at or GPS,
and provider is the marketplace code (or `aggregator`), never `lalamove` — every
courier sweep is gated on exactly those, so nothing here can be dispatched.

Guarded so it is a no-op on a re-run and never fights the live mirror: it inserts
only where no `order_deliveries` / `order_drivers` row exists yet. The old
`aggregator_driver_*` columns are left in place (readers migrate first).

Revision ID: 166_agg_fulfil_backfill
Revises: 165_promo_category_scope
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "166_agg_fulfil_backfill"
down_revision: Union[str, None] = "165_promo_category_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Marketplace channel (display name) → our courier code. Mirrors
#: courier_catalog.code_for_channel for the five channels seen in prod; the ELSE
#: is a generic `aggregator` so an unknown channel still gets an inert row.
_CHANNEL_CASE = """
    CASE trim(regexp_replace(lower(coalesce(o.aggregator_channel, '')),
                             '[^a-z ]', '', 'g'))
        WHEN 'careem' THEN 'careem'
        WHEN 'deliveroo' THEN 'deliveroo'
        WHEN 'talabat' THEN 'talabat'
        WHEN 'noon food' THEN 'noon'
        WHEN 'noon' THEN 'noon'
        WHEN 'keeta' THEN 'keeta'
        ELSE 'aggregator'
    END
"""


def upgrade() -> None:
    # 1. One inert delivery row per aggregator DELIVERY order that lacks one.
    op.execute(
        f"""
        INSERT INTO order_deliveries (
            order_id, provider, fee_charged, courier_status, cancel_reason,
            driver_name, driver_phone, driver_assigned_at, driver_assignment_count,
            booked_at, previous_courier_order_ids, dispatch_attempts
        )
        SELECT o.id,
               {_CHANNEL_CASE},
               o.aggregator_delivery_fee,
               o.aggregator_driver_status,
               o.aggregator_cancel_reason,
               o.aggregator_driver_name,
               o.aggregator_driver_phone,
               CASE WHEN o.aggregator_driver_name IS NOT NULL
                         OR o.aggregator_driver_phone IS NOT NULL
                    THEN o.created_at END,
               CASE WHEN o.aggregator_driver_name IS NOT NULL
                         OR o.aggregator_driver_phone IS NOT NULL
                    THEN 1 ELSE 0 END,
               o.created_at,
               '[]'::jsonb,
               0
        FROM orders o
        WHERE o.source = 'aggregator'
          AND o.delivery_method = 'delivery'
          AND NOT EXISTS (
              SELECT 1 FROM order_deliveries d WHERE d.order_id = o.id
          )
        """
    )

    # 2. The rider's stint (one active row) for those that carry a driver.
    op.execute(
        """
        INSERT INTO order_drivers (
            order_id, provider, name, phone, sequence, is_active, assigned_at
        )
        SELECT d.order_id, d.provider, d.driver_name, d.driver_phone,
               1, true, coalesce(d.driver_assigned_at, d.created_at)
        FROM order_deliveries d
        JOIN orders o ON o.id = d.order_id
        WHERE o.source = 'aggregator'
          AND (d.driver_name IS NOT NULL OR d.driver_phone IS NOT NULL)
          AND NOT EXISTS (
              SELECT 1 FROM order_drivers dr WHERE dr.order_id = d.order_id
          )
        """
    )


def downgrade() -> None:
    # A backfill of a mirror; the source columns still hold the truth. No-op.
    pass
