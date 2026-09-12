"""Switch three Dubai zones from Slider car to Lalamove.

A seven-probe median (2026-09-11, across the afternoon, the dinner peak and late
off-peak) put Lalamove cheaper than Slider by AED 4–5 in Palm Jumeirah, Business
Bay and Discovery Gardens — comfortably past the reliability margin — so their
primary courier moves to Lalamove. Each keeps ``slider_car`` as its first manual
fallback (the Lalamove default since ``227_lalamove_slider_fallback``).

An in-place edit of the active map's three rows, guarded to the exact prior
state so a replay, or a run against a console-edited database, matches nothing.
The rest of the map is deliberately left untouched.
"""

from __future__ import annotations

from alembic import op

revision = "232_meydan_area_lala"
down_revision = "231_transfer_auto_printed_at"
branch_labels = None
depends_on = None

_ZONES = ("Dubai · Palm Jumeirah", "Dubai · Business Bay", "Dubai · Discovery Gardens")


def _in_clause() -> str:
    return ", ".join(f"'{name}'" for name in _ZONES)


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE delivery_polygons
           SET fulfilment_provider = 'lalamove',
               alternate_providers = '["slider_car", "third_party"]'::jsonb
         WHERE name IN ({_in_clause()})
           AND fulfilment_provider = 'slider_car'
           AND version_id IN (
               SELECT id FROM delivery_polygon_versions WHERE is_active
           )
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE delivery_polygons
           SET fulfilment_provider = 'slider_car',
               alternate_providers = '["lalamove", "third_party"]'::jsonb
         WHERE name IN ({_in_clause()})
           AND fulfilment_provider = 'lalamove'
           AND version_id IN (
               SELECT id FROM delivery_polygon_versions WHERE is_active
           )
        """
    )
