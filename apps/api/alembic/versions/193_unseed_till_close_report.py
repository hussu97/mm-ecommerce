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
    op.execute(
        """
        DELETE FROM inventory_report_templates
         WHERE name = 'End-of-shift finished goods count'
           AND report_type = 'finished_goods'
           AND cadence = 'per_till'
           AND version_number = 1
        """
    )


def downgrade() -> None:
    # Forward-only: this un-seed does not re-seed. 192's own upgrade is the seed.
    pass
