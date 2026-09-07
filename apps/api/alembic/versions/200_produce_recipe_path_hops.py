"""Rewrite production rows' recipe_path from the retired flat-dict shape to hop-lists.

``transfer_service.produce`` used to write ``recipe_path`` as a flat list of dicts
(``[{"recipe_version_id": …, "owner_id": …}]``) while every other movement — order
consumption, returns, waste — writes a list of *paths*, each a list of hop dicts
(``list[list[dict[str, str]]]``). That one exception is why the shared schema had
to widen to ``list[Any]``, and why ``recipe_path`` serialised inconsistently. The
code now emits hop-lists everywhere; this migration brings the existing production
rows into the same shape so the schema can narrow back.

Guarded (canon rule 7): touches only rows on ``production`` transactions whose
``recipe_path`` is a non-empty array of *objects* (the flat shape). A row already
in hop-list shape has an *array* as its first element and matches nothing, so this
is idempotent and safe to re-run. Production has ~0 such rows today.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "200_produce_recipe_path_hops"
down_revision: Union[str, None] = "199_agg_ref_unique_per_day"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Wrap each flat dict in its own single-hop path: [a, b] -> [[a], [b]].
    op.execute(
        """
        UPDATE inventory_transaction_items AS iti
           SET recipe_path = (
               SELECT jsonb_agg(jsonb_build_array(elem))
                 FROM jsonb_array_elements(iti.recipe_path) AS elem
           )
          FROM inventory_transactions AS t
         WHERE t.id = iti.transaction_id
           AND t.type = 'production'
           AND jsonb_typeof(iti.recipe_path) = 'array'
           AND jsonb_array_length(iti.recipe_path) > 0
           AND jsonb_typeof(iti.recipe_path -> 0) = 'object'
        """
    )


def downgrade() -> None:
    # Flatten each single-hop path back to the flat dict: [[a], [b]] -> [a, b].
    op.execute(
        """
        UPDATE inventory_transaction_items AS iti
           SET recipe_path = (
               SELECT jsonb_agg(elem -> 0)
                 FROM jsonb_array_elements(iti.recipe_path) AS elem
           )
          FROM inventory_transactions AS t
         WHERE t.id = iti.transaction_id
           AND t.type = 'production'
           AND jsonb_typeof(iti.recipe_path) = 'array'
           AND jsonb_array_length(iti.recipe_path) > 0
           AND jsonb_typeof(iti.recipe_path -> 0) = 'array'
        """
    )
