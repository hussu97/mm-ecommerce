"""Drop 'production' from the report-template item input CHECK — it had no posting type.

``required_input = 'production'`` was accepted by the schema and this CHECK
constraint, but ``report_service.post_report`` has no ``inputs_to_type`` entry for
it, so submitting a report that held it raised BadRequestError and blocked the
till until close. Production is posted through the report's Produced *column*
(``report_columns``), not through a per-item input, so 'production' as an item
input is a dead value. Re-map any existing rows to 'physical_count' and narrow the
constraint to the four inputs that post.

Guarded (canon rule 7): the re-map matches only the exact retired value, so a row
an operator later sets is never disturbed; the constraint swap is structural.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "201_report_input_drop_production"
down_revision: Union[str, None] = "200_produce_recipe_path_hops"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "ck_inventory_report_template_item_input"
_TABLE = "inventory_report_template_items"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE {_TABLE}
           SET required_input = 'physical_count'
         WHERE required_input = 'production'
        """
    )
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(
        f"""
        ALTER TABLE {_TABLE}
          ADD CONSTRAINT {_CONSTRAINT}
          CHECK (required_input IN
                 ('physical_count', 'internal_use', 'waste', 'receipt'))
        """
    )


def downgrade() -> None:
    # Widen the constraint back to allow 'production' again. The re-mapped rows
    # are left as 'physical_count' — a valid value under both constraints.
    op.execute(f"ALTER TABLE {_TABLE} DROP CONSTRAINT {_CONSTRAINT}")
    op.execute(
        f"""
        ALTER TABLE {_TABLE}
          ADD CONSTRAINT {_CONSTRAINT}
          CHECK (required_input IN
                 ('physical_count', 'production', 'internal_use', 'waste', 'receipt'))
        """
    )
