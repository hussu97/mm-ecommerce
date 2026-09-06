"""Seed an at-till-close finished-goods count for each shop.

The inventory-report-at-till-close machinery is fully built (templates, the
`/pos/inventory/tasks` pull, the guided POS entry) but nothing surfaced at close
because no branch had a `per_till` template — cadence `per_till` is the "at till
close" trigger, and templates are operator-created, none seeded. So a cashier
closed the till and saw nothing to count.

This seeds one per shop: a `per_till`, `finished_goods` template holding the
finished-goods items (kinds `produced_good` / `resale_good`), so the register
shows the count sheet at close. It is **not required** — staff can skip or defer
it — so it never blocks a close; a shop that wants it mandatory flips that in the
console.

Guarded and idempotent (canon rule 7): skipped for a branch that already has an
active `per_till` finished-goods template, and a no-op entirely where there are
no finished-goods items to count. Editing the template in the console later does
not fight this, because it only ever inserts where none exists.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "192_till_close_inv_report"
down_revision: Union[str, None] = "191_menu_root_groups"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            v_items uuid[];
            v_branch uuid;
            v_template uuid;
            v_item uuid;
            v_order int;
        BEGIN
            -- The finished goods a shop counts at close (up to 8, stable order).
            SELECT array_agg(id ORDER BY name)
              INTO v_items
              FROM (
                SELECT id, name
                  FROM inventory_items
                 WHERE is_active = true
                   AND kind IN ('produced_good', 'resale_good')
                 ORDER BY name
                 LIMIT 8
              ) AS picked;

            IF v_items IS NULL OR array_length(v_items, 1) IS NULL THEN
                RETURN;  -- nothing to count anywhere; leave it alone
            END IF;

            FOR v_branch IN
                SELECT id FROM branches WHERE is_active = true
            LOOP
                -- Skip a shop that already has an active per-till finished-goods
                -- template — the operator (or an earlier run) owns it.
                IF EXISTS (
                    SELECT 1 FROM inventory_report_templates
                     WHERE branch_id = v_branch
                       AND report_type = 'finished_goods'
                       AND cadence = 'per_till'
                       AND is_active = true
                ) THEN
                    CONTINUE;
                END IF;

                v_template := gen_random_uuid();
                INSERT INTO inventory_report_templates
                    (id, branch_id, name, report_type, cadence, is_required,
                     is_active, version_number, configuration,
                     approval_cost_threshold, approval_variance_percent,
                     created_at, updated_at)
                VALUES
                    (v_template, v_branch, 'End-of-shift finished goods count',
                     'finished_goods', 'per_till', false, true, 1,
                     '{"visible_columns": ["opening","movements","expected","physical","variance","remark"]}'::jsonb,
                     100, 10, now(), now());

                v_order := 0;
                FOREACH v_item IN ARRAY v_items LOOP
                    INSERT INTO inventory_report_template_items
                        (id, template_id, item_id, display_order, required_input)
                    VALUES
                        (gen_random_uuid(), v_template, v_item, v_order,
                         'physical_count');
                    v_order := v_order + 1;
                END LOOP;
            END LOOP;
        END $$;
        """
    )


def downgrade() -> None:
    # Remove only what this seed creates — the named, seeded template and (by
    # cascade) its items. A template an operator has since edited keeps its
    # revised name and is left alone.
    op.execute(
        """
        DELETE FROM inventory_report_templates
         WHERE name = 'End-of-shift finished goods count'
           AND report_type = 'finished_goods'
           AND cadence = 'per_till'
           AND version_number = 1
        """
    )
