"""Give the mixed boxes the choice rules Foodics actually holds.

The Foodics importer used to guess the rules on a product↔modifier link
instead of reading them, and set `maximum_options` to the *number of options
in the group*. "Mix Brownies Box Of 3" therefore came in as

    minimum 0, maximum 9, unique options

— pick nothing, or pick all nine, and never two of the same — where the
Foodics console says minimum 3, maximum 3, free 0. Nobody could sell a box of
three brownies as two Ferrero and one Fudge, which is the entire product.

The importer is fixed at source; this repairs the rows it already wrote.

Only the groups whose name carries the box size are touched, because that is
the only place the size survived the bad import: the importer wrote the
maximum away, but Foodics' group names — "Options (Max 3)", "Options (Max 9)"
— still say it. Every other modifier link is left exactly as it is.

Revision ID: 046_mix_box_choice_rules
Revises: 045_product_sales_channels
Create Date: 2026-07-28
"""

from typing import Sequence, Union

from alembic import op

revision: str = "046_mix_box_choice_rules"
down_revision: Union[str, None] = "045_product_sales_channels"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Matches "Options (Max 3)" and captures the 3. Anchored on the closing
#: bracket so "Max 3 pieces" — a name that means something else — is not
#: mistaken for a size.
_SIZE_IN_NAME = r"\(\s*[Mm]ax\s+(\d+)\s*\)\s*$"


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE product_modifiers pm
        SET minimum_options = (substring(m.name FROM '{_SIZE_IN_NAME}'))::int,
            maximum_options = (substring(m.name FROM '{_SIZE_IN_NAME}'))::int,
            free_options = 0,
            -- The whole point of a mixed box: three brownies, not three
            -- different brownies.
            unique_options = false
        FROM modifiers m
        WHERE m.id = pm.modifier_id
          AND m.name ~ '{_SIZE_IN_NAME}'
          AND (substring(m.name FROM '{_SIZE_IN_NAME}'))::int > 0
        """
    )


def downgrade() -> None:
    # The pre-repair values were a guess, not a record — `maximum_options` was
    # the group's option count and there is nothing to restore it from but the
    # group itself. Reconstructing that guess is the honest inverse.
    op.execute(
        f"""
        UPDATE product_modifiers pm
        SET minimum_options = 0,
            maximum_options = greatest(
                (SELECT count(*) FROM modifier_options o
                 WHERE o.modifier_id = pm.modifier_id), 1
            ),
            unique_options = true
        FROM modifiers m
        WHERE m.id = pm.modifier_id
          AND m.name ~ '{_SIZE_IN_NAME}'
        """
    )
