"""Correct paper-count raw-material units and seed two missing inputs.

This is catalogue metadata only: no level, ledger or availability row is touched.
The spreadsheet import remains the reviewable path for any later Foodics correction.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "187_inventory_units_seed"
down_revision: Union[str, None] = "186_inventory_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The daily Consumption & Procurement Schedule records the physical units
    # staff count. Match immutable SKUs, never display names (Cream Cheese has
    # two distinct SKUs in the live catalogue).
    op.execute(
        """
        UPDATE inventory_items AS item
        SET storage_unit = source.unit,
            ingredient_unit = source.unit,
            storage_to_ingredient_factor = 1
        FROM (VALUES
          ('RM001', 'g'), ('RM002', 'unit'), ('RM003', 'g'), ('RM004', 'g'),
          ('RM005', 'g'), ('RM006', 'g'), ('RM007', 'g'), ('RM008', 'g'),
          ('RM009', 'g'), ('RM010', 'l'), ('RM011', 'g'), ('RM012', 'g'),
          ('RM013', 'g'), ('RM014', 'unit'), ('RM015', 'g'), ('RM016', 'g'),
          ('RM017', 'g'), ('RM018', 'g'), ('RM019', 'unit'), ('RM020', 'unit'),
          ('RM021', 'unit'), ('RM022', 'unit'), ('RM023', 'unit'),
          ('RM024', 'bottle'), ('RM025', 'g'), ('RM026', 'unit'), ('RM028', 'g')
        ) AS source(sku, unit)
        WHERE item.sku = source.sku
          AND item.storage_unit = 'unit'
          AND item.ingredient_unit = 'unit'
          AND item.storage_to_ingredient_factor = 1
        """
    )
    # Both are written on the source schedule but absent from the live Foodics
    # extraction. Their physical unit was not written on the sheet, so they are
    # explicitly marked as generic units for staff review rather than guessed as
    # grams. They start with no stock and never affect storefront availability.
    op.execute(
        """
        INSERT INTO inventory_items (
          id, sku, name, kind, tracking_mode, storage_unit, ingredient_unit,
          storage_to_ingredient_factor, minimum_level, maximum_level, par_level,
          cost, costing_method, yield_percentage, is_product, is_active,
          translations, created_at, updated_at
        ) VALUES
          (gen_random_uuid(), 'RM031', 'Lotus Biscoff Filling', 'raw_material',
           'stocked', 'unit', 'unit', 1, 0, 0, 0, 0, 'fixed', 1, false, true,
           '{}'::jsonb, now(), now()),
          (gen_random_uuid(), 'RM032', 'Kinder Filling', 'raw_material',
           'stocked', 'unit', 'unit', 1, 0, 0, 0, 0, 'fixed', 1, false, true,
           '{}'::jsonb, now(), now())
        ON CONFLICT (sku) DO NOTHING
        """
    )


def downgrade() -> None:
    # The migration intentionally does not erase operating catalogue data.
    # Restoring generic units or deleting potentially edited rows would be more
    # destructive than a no-op downgrade.
    pass
