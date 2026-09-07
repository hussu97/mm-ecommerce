"""Undo the at-till-close seed from 192 — the branch reports are operator-owned.

192 seeded a per_till finished-goods template per branch. That was wrong: the
shops configure their own till-close reports in the console, and a seeded one
sits alongside them as a duplicate. This removes only what 192 created, matched
by its exact seeded signature (name + type + cadence + version 1), so an
operator-configured report — which has a different name and/or is a later
revision — is never touched. Cascades to the seeded template's items.

Idempotent: deletes nothing on a database where the seed never ran or was already
cleaned.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "193_unseed_till_close_report"
down_revision: Union[str, None] = "192_till_close_inv_report"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # shift_inventory_reports.template_id references this row ON DELETE RESTRICT,
    # so a plain DELETE aborts the whole deploy half-applied the moment a till has
    # closed against the seeded template. Deactivate any referenced seed (it stops
    # being offered at close, same practical effect as removing it) and delete only
    # the untouched ones. Same guarded shape as 192's downgrade.
    op.execute(
        """
        UPDATE inventory_report_templates t
           SET is_active = false
         WHERE t.name = 'End-of-shift finished goods count'
           AND t.report_type = 'finished_goods'
           AND t.cadence = 'per_till'
           AND t.version_number = 1
           AND EXISTS (
               SELECT 1 FROM shift_inventory_reports r WHERE r.template_id = t.id
           )
        """
    )
    op.execute(
        """
        DELETE FROM inventory_report_templates t
         WHERE t.name = 'End-of-shift finished goods count'
           AND t.report_type = 'finished_goods'
           AND t.cadence = 'per_till'
           AND t.version_number = 1
           AND NOT EXISTS (
               SELECT 1 FROM shift_inventory_reports r WHERE r.template_id = t.id
           )
        """
    )


def downgrade() -> None:
    # Forward-only: this un-seed does not re-seed. 192's own upgrade is the seed.
    pass
