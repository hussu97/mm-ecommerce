"""A Lalamove zone falls back to Slider (car) before the third party.

The active map's per-polygon `alternate_providers` were baked in when the map
was seeded, so changing the policy constants (`DEFAULT_ALTERNATES` in the model,
`ALTERNATES` in `scripts/build_delivery_areas.py`) does not touch the rows that
are already live. This carries the same change onto the running map.

Every Lalamove zone is one Slider reaches — Dubai, Ras al-Khaimah and Umm
al-Quwain all have Slider (car) fares — so Slider (car) is inserted ahead of the
third party as the escape to try before the manual one. A Lalamove zone still is
*not* offered noon Send: noon Send cannot cross an emirate boundary or carry a
run past 20 km, so a zone we gave to Lalamove is one it probably cannot reach.

Scoped to the **active** version's Lalamove polygons only. Older versions are
rollback targets and are left describing the map as it was published; a new map
built by the generator already carries the new fallback from `ALTERNATES`.

**Guarded, per convention 7.** The upgrade matches only rows still holding the
exact old value `'["third_party"]'` and the downgrade only rows holding the exact
new value, so a replay — or a run against a database an operator has since edited
in the console — matches nothing and does nothing.

Revision ID: 227_lalamove_slider_fb
Revises: 226_transfer_template_versions
Create Date: 2026-09-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "227_lalamove_slider_fb"
down_revision: Union[str, None] = "226_transfer_template_versions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE delivery_polygons
               SET alternate_providers = '["slider_car", "third_party"]'::jsonb
             WHERE fulfilment_provider = 'lalamove'
               AND alternate_providers = '["third_party"]'::jsonb
               AND version_id IN (
                   SELECT id FROM delivery_polygon_versions WHERE is_active
               )
            """
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE delivery_polygons
               SET alternate_providers = '["third_party"]'::jsonb
             WHERE fulfilment_provider = 'lalamove'
               AND alternate_providers = '["slider_car", "third_party"]'::jsonb
               AND version_id IN (
                   SELECT id FROM delivery_polygon_versions WHERE is_active
               )
            """
        )
    )
