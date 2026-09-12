"""Retire the legacy bare `slider` courier code; migrate its data to `slider_car`.

Slider prices a bike and a car differently, and every zone, quote and new
dispatch has named the *tier* (`slider_bike` / `slider_car`) since `174`. The
bare `slider` code that predated the split no longer has any code path that
writes it, so it is removed from the catalogue and from the enum; the only thing
left is old data.

This moves that data to `slider_car` — the safe direction, since the tier is
computed from distance and a bare-`slider` run that could not be a bike was a
car. The columns that hold a provider *code as data* are plain text with no
CHECK or enum, so these are unguarded `UPDATE`s, each scoped to the exact legacy
value so a re-run — or a database restored from an older dump — is a no-op.

Two `"slider"` strings are deliberately NOT touched, because they are not the
courier code and `slider_bike`/`slider_car` depend on them:

* the delivery **status-family** key (one status vocabulary for all Slider
  tiers, in `order_delivery._status_family`); and
* Slider-the-company's **webhook source** identity (`webhook_events.provider` /
  `webhook_logs.provider`, `/webhooks/slider`), which is one channel whatever
  vehicle carried the order — migrating it would misreport the source and break
  webhook dedup.

Revision ID: 241_drop_legacy_slider
Revises: 240_order_receivers
Create Date: 2026-09-12
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "241_drop_legacy_slider"
down_revision: Union[str, None] = "240_order_receivers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The delivery record's provider, and the pre-reassignment provider it kept.
    op.execute(
        "UPDATE order_deliveries SET provider = 'slider_car' WHERE provider = 'slider'"
    )
    op.execute(
        "UPDATE order_deliveries SET original_provider = 'slider_car' "
        "WHERE original_provider = 'slider'"
    )
    # The courier's own word on each driver stint.
    op.execute(
        "UPDATE order_drivers SET provider = 'slider_car' WHERE provider = 'slider'"
    )
    # The zone's preferred courier.
    op.execute(
        "UPDATE delivery_polygons SET fulfilment_provider = 'slider_car' "
        "WHERE fulfilment_provider = 'slider'"
    )
    # `alternate_providers` is a JSONB array of codes, tried in order. Map any
    # bare `slider` element to `slider_car`, dedupe (an existing `slider_car`
    # would otherwise appear twice) while keeping the first position each code
    # held, and rewrite only the rows that actually contain it.
    op.execute(
        """
        UPDATE delivery_polygons AS p
        SET alternate_providers = COALESCE((
            SELECT jsonb_agg(elem ORDER BY min_ord)
            FROM (
                SELECT elem, MIN(ord) AS min_ord
                FROM (
                    SELECT
                        CASE WHEN value = 'slider' THEN 'slider_car' ELSE value END
                            AS elem,
                        ord
                    FROM jsonb_array_elements_text(p.alternate_providers)
                        WITH ORDINALITY AS x(value, ord)
                ) mapped
                GROUP BY elem
            ) deduped
        ), '[]'::jsonb)
        WHERE p.alternate_providers @> '["slider"]'::jsonb
        """
    )
    # The catalogue row itself. `slider_bike`/`slider_car` keep their rows (from
    # `174`); the promise for a migrated order is now read off `slider_car`.
    op.execute("DELETE FROM couriers WHERE code = 'slider'")


def downgrade() -> None:
    # The data merge cannot be un-done — a `slider_car` row cannot be told from
    # one that was `slider_car` all along — so this only restores the catalogue
    # row. Its columns are the `couriers` shape as of this revision (the batching
    # columns `127_slider_courier` also set were dropped since); every other
    # NOT NULL column has a default. Idempotent for a dump that still has it.
    op.execute(
        sa.text(
            "INSERT INTO couriers (code, name, unbatched_promise_kind, "
            "unbatched_promise_minutes) "
            "VALUES ('slider', 'Slider', 'minutes', 90) "
            "ON CONFLICT (code) DO NOTHING"
        )
    )
