"""Classify the live inventory catalogue without changing stock or recipes.

The Foodics inventory export identifies which records are direct retail products
(`is_product`) and the supplied paper count sheets identify physical raw and
packaging groups. This migration records that operational taxonomy while leaving
all balances, availability, costs, and recipes untouched. Recipe lines continue
to be staged only from a complete Foodics detail snapshot.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "188_inventory_classify"
down_revision: Union[str, None] = "187_inventory_units_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # References are stable import keys; names remain editable by operations.
    # Do not overwrite a category that an operator has already tailored.
    op.execute(
        """
        INSERT INTO inventory_categories
          (id, name, reference, display_order, is_active, translations, created_at, updated_at)
        VALUES
          (gen_random_uuid(), 'Raw Materials', 'raw-materials', 10, true, '{}'::jsonb, now(), now()),
          (gen_random_uuid(), 'Packaging Materials', 'packaging-materials', 20, true, '{}'::jsonb, now(), now()),
          (gen_random_uuid(), 'Finished Goods', 'finished-goods', 30, true, '{}'::jsonb, now(), now()),
          (gen_random_uuid(), 'Retail Resale Goods', 'retail-resale-goods', 40, true, '{}'::jsonb, now(), now())
        ON CONFLICT (reference) DO NOTHING
        """
    )

    # Every Foodics inventory record is physically counted, received, or made;
    # they therefore remain `stocked`. A phantom is reserved for a future,
    # explicitly non-counted preparation/sub-recipe — it must never be inferred
    # merely because an item has ingredients.
    #
    # Guard category_id so this deploy cannot overwrite a live operator's
    # classification. The kind correction is similarly limited to the legacy
    # default (`raw_material`) except for an already-correct resale item.
    op.execute(
        """
        WITH source(sku, category_reference, kind, is_product) AS (
          VALUES
            ('RM001', 'raw-materials', 'raw_material', false),
            ('RM002', 'raw-materials', 'raw_material', false),
            ('RM003', 'raw-materials', 'raw_material', false),
            ('RM004', 'raw-materials', 'raw_material', false),
            ('RM005', 'raw-materials', 'raw_material', false),
            ('RM006', 'raw-materials', 'raw_material', false),
            ('RM007', 'raw-materials', 'raw_material', false),
            ('RM008', 'raw-materials', 'raw_material', false),
            ('RM009', 'raw-materials', 'raw_material', false),
            ('RM010', 'raw-materials', 'raw_material', false),
            ('RM011', 'raw-materials', 'raw_material', false),
            ('RM012', 'raw-materials', 'raw_material', false),
            ('RM013', 'raw-materials', 'raw_material', false),
            ('RM014', 'raw-materials', 'raw_material', false),
            ('RM015', 'raw-materials', 'raw_material', false),
            ('RM016', 'raw-materials', 'raw_material', false),
            ('RM017', 'raw-materials', 'raw_material', false),
            ('RM018', 'raw-materials', 'raw_material', false),
            ('RM019', 'raw-materials', 'raw_material', false),
            ('RM020', 'raw-materials', 'raw_material', false),
            ('RM021', 'raw-materials', 'raw_material', false),
            ('RM022', 'raw-materials', 'raw_material', false),
            ('RM023', 'raw-materials', 'raw_material', false),
            ('RM024', 'raw-materials', 'raw_material', false),
            ('RM025', 'raw-materials', 'raw_material', false),
            ('RM026', 'raw-materials', 'raw_material', false),
            ('RM027', 'raw-materials', 'raw_material', false),
            ('RM028', 'raw-materials', 'raw_material', false),
            ('RM029', 'raw-materials', 'raw_material', false),
            ('RM030', 'raw-materials', 'raw_material', false),
            ('RM031', 'raw-materials', 'raw_material', false),
            ('RM032', 'raw-materials', 'raw_material', false),
            ('PKM0001', 'packaging-materials', 'packaging', false),
            ('PKM0002', 'packaging-materials', 'packaging', false),
            ('PKM0003', 'packaging-materials', 'packaging', false),
            ('PKM0004', 'packaging-materials', 'packaging', false),
            ('PKM0005', 'packaging-materials', 'packaging', false),
            ('PKM0006', 'packaging-materials', 'packaging', false),
            ('PKM0007', 'packaging-materials', 'packaging', false),
            ('PKM0008', 'packaging-materials', 'packaging', false),
            ('PKM0009', 'packaging-materials', 'packaging', false),
            ('B0001', 'finished-goods', 'produced_good', false),
            ('B0002', 'finished-goods', 'produced_good', false),
            ('B0003', 'finished-goods', 'produced_good', false),
            ('B0004', 'finished-goods', 'produced_good', false),
            ('B0005', 'finished-goods', 'produced_good', false),
            ('B0006', 'finished-goods', 'produced_good', false),
            ('B0007', 'finished-goods', 'produced_good', false),
            ('B0008', 'finished-goods', 'produced_good', false),
            ('B0009', 'finished-goods', 'produced_good', false),
            ('B0010', 'finished-goods', 'produced_good', false),
            ('C0001', 'finished-goods', 'produced_good', false),
            ('C0002', 'finished-goods', 'produced_good', false),
            ('C0003', 'finished-goods', 'produced_good', false),
            ('C0004', 'finished-goods', 'produced_good', false),
            ('C0005', 'finished-goods', 'produced_good', false),
            ('C0006', 'finished-goods', 'produced_good', false),
            ('C0007', 'finished-goods', 'produced_good', false),
            ('CM0001', 'finished-goods', 'produced_good', false),
            ('CM0002', 'finished-goods', 'produced_good', false),
            ('CM0003', 'finished-goods', 'produced_good', false),
            ('CM0004', 'finished-goods', 'produced_good', false),
            ('CM0005', 'finished-goods', 'produced_good', false),
            ('CM0006', 'finished-goods', 'produced_good', false),
            ('CS0001', 'finished-goods', 'produced_good', false),
            ('CS0002', 'finished-goods', 'produced_good', false),
            ('CS0003', 'finished-goods', 'produced_good', false),
            ('CS0004', 'finished-goods', 'produced_good', false),
            ('CS0005', 'finished-goods', 'produced_good', false),
            ('D0001', 'finished-goods', 'produced_good', false),
            ('D0002', 'finished-goods', 'produced_good', false),
            ('FG0050', 'retail-resale-goods', 'resale_good', true),
            ('FG0058', 'retail-resale-goods', 'resale_good', true),
            ('FG0059', 'retail-resale-goods', 'resale_good', true),
            ('FG0060', 'retail-resale-goods', 'resale_good', true),
            ('FG0061', 'retail-resale-goods', 'resale_good', true),
            ('FG0062', 'retail-resale-goods', 'resale_good', true),
            ('FG0063', 'retail-resale-goods', 'resale_good', true),
            ('FG0064', 'retail-resale-goods', 'resale_good', true),
            ('FG0065', 'retail-resale-goods', 'resale_good', true),
            ('FG0066', 'retail-resale-goods', 'resale_good', true),
            ('FG0067', 'retail-resale-goods', 'resale_good', true),
            ('FG0068', 'retail-resale-goods', 'resale_good', true),
            ('FG0069', 'retail-resale-goods', 'resale_good', true),
            ('FG0070', 'retail-resale-goods', 'resale_good', true),
            ('FG0071', 'retail-resale-goods', 'resale_good', true),
            ('FG0072', 'retail-resale-goods', 'resale_good', true),
            ('FG0073', 'retail-resale-goods', 'resale_good', true),
            ('FG0074', 'retail-resale-goods', 'resale_good', true),
            ('FG0075', 'retail-resale-goods', 'resale_good', true),
            ('FG0076', 'retail-resale-goods', 'resale_good', true),
            ('FG0077', 'retail-resale-goods', 'resale_good', true),
            ('FG0078', 'retail-resale-goods', 'resale_good', true),
            ('FG0079', 'retail-resale-goods', 'resale_good', true),
            ('FG0080', 'retail-resale-goods', 'resale_good', true),
            ('FG0081', 'retail-resale-goods', 'resale_good', true),
            ('FG0118', 'retail-resale-goods', 'resale_good', true),
            ('FG0124', 'retail-resale-goods', 'resale_good', true),
            ('FG0127', 'retail-resale-goods', 'resale_good', true),
            ('FG0128', 'retail-resale-goods', 'resale_good', true)
        )
        UPDATE inventory_items AS item
        SET category_id = category.id,
            kind = CASE
              WHEN item.kind = 'raw_material' OR item.kind = source.kind
                THEN source.kind
              ELSE item.kind
            END,
            is_product = source.is_product,
            updated_at = now()
        FROM source
        JOIN inventory_categories AS category
          ON category.reference = source.category_reference
        WHERE item.sku = source.sku
          AND item.category_id IS NULL
          AND item.tracking_mode = 'stocked'
        """
    )


def downgrade() -> None:
    # Do not erase classifications which may have since been reviewed or used by
    # staff. A forward correction is the safe inventory-audit operation.
    pass
