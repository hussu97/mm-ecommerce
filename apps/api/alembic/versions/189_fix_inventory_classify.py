"""Finish classifying records still attached to legacy blank-reference categories.

Revision 188 correctly created the canonical categories but only updated rows with
``category_id IS NULL``.  The live Foodics seed had already attached some rows to
legacy categories (for example ``Dairy``) whose reference is NULL, leaving the
catalogue split between operational and canonical categories.  This is a
forward-only, deliberately narrow repair: it changes only the audited SKUs while
their category is still one of those unreferenced legacy categories.

No stock, level, ledger, recipe, availability, or cost data is changed.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "189_fix_inventory_classify"
down_revision: Union[str, None] = "188_inventory_classify"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the catalogue audit explicit.  These patterns encode only the known
    # Foodics/MM SKU families, rather than classifying a future similarly named
    # SKU without operator review.
    op.execute(
        """
        WITH source AS (
          SELECT
            item.sku,
            CASE
              WHEN item.sku ~ '^RM0(0[1-9]|[12][0-9]|3[0-2])$'
                THEN 'raw-materials'
              WHEN item.sku ~ '^PKM000[1-9]$'
                THEN 'packaging-materials'
              WHEN item.sku ~ '^(B000[1-9]|B0010|C000[1-7]|CM000[1-6]|CS000[1-5]|D000[1-2])$'
                THEN 'finished-goods'
              WHEN item.sku ~ '^(FG0050|FG00(5[8-9]|6[0-9]|7[0-9]|80|81)|FG0118|FG0124|FG0127|FG0128)$'
                THEN 'retail-resale-goods'
            END AS category_reference,
            CASE
              WHEN item.sku ~ '^RM0(0[1-9]|[12][0-9]|3[0-2])$' THEN 'raw_material'
              WHEN item.sku ~ '^PKM000[1-9]$' THEN 'packaging'
              WHEN item.sku ~ '^(B000[1-9]|B0010|C000[1-7]|CM000[1-6]|CS000[1-5]|D000[1-2])$' THEN 'produced_good'
              ELSE 'resale_good'
            END AS kind,
            item.sku ~ '^(FG0050|FG00(5[8-9]|6[0-9]|7[0-9]|80|81)|FG0118|FG0124|FG0127|FG0128)$' AS is_product
          FROM inventory_items AS item
          WHERE item.sku ~ '^(RM0(0[1-9]|[12][0-9]|3[0-2])|PKM000[1-9]|B000[1-9]|B0010|C000[1-7]|CM000[1-6]|CS000[1-5]|D000[1-2]|FG0050|FG00(5[8-9]|6[0-9]|7[0-9]|80|81)|FG0118|FG0124|FG0127|FG0128)$'
        )
        UPDATE inventory_items AS item
        SET category_id = canonical_category.id,
            kind = source.kind,
            is_product = source.is_product,
            updated_at = now()
        FROM source
        JOIN inventory_categories AS canonical_category
          ON canonical_category.reference = source.category_reference
        WHERE item.sku = source.sku
          AND item.tracking_mode = 'stocked'
          AND EXISTS (
            SELECT 1
            FROM inventory_categories AS current_category
            WHERE current_category.id = item.category_id
              AND current_category.reference IS NULL
          )
        """
    )


def downgrade() -> None:
    # Classification is audit data.  Do not restore deprecated category links
    # and accidentally erase any subsequent review.
    pass
