"""Re-key aggregator option snapshots to the shape every reader expects.

The order-ingest wrote each selected option as `{name, price, ...}`, but the
admin item table and the register read `option_name` / `option_price` /
`option_id` (the same shape a website order stores). So an aggregator order's
modifiers showed as a bare "1 ×" with no name. The ingest now writes the
canonical keys; this backfills the orders already recorded with the old shape.

Guarded per element: only an option that still has `name` and lacks
`option_name` is rewritten, so a re-run — or a row already in the new shape —
matches nothing and is left alone.

Revision ID: 135_agg_option_keys
Revises: 134_courier_logos_gcs
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "135_agg_option_keys"
down_revision: Union[str, None] = "134_courier_logos_gcs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE order_items oi
        SET selected_options_snapshot = (
            SELECT jsonb_agg(
                (elem - 'name' - 'price')
                || jsonb_build_object(
                    'option_name', elem->'name',
                    'option_price', elem->'price',
                    'option_id', elem->'modifier_option_id',
                    'modifier_name', NULL
                )
            )
            FROM jsonb_array_elements(oi.selected_options_snapshot) elem
        )
        FROM orders o
        WHERE o.id = oi.order_id
          AND o.source = 'aggregator'
          AND jsonb_typeof(oi.selected_options_snapshot) = 'array'
          AND EXISTS (
              SELECT 1
              FROM jsonb_array_elements(oi.selected_options_snapshot) e
              WHERE e ? 'name' AND NOT (e ? 'option_name')
          )
        """
    )


def downgrade() -> None:
    # One-way: the old shape was a bug, and reversing a data-cleanup only to
    # reintroduce it helps nothing.
    pass
