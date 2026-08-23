"""noon Send promises ninety minutes, not sixty.

Asked for on 2026-08-18 alongside making the number configurable at all. `106`
gave it a column and the admin's delivery Estimates screen gives it a form; this
is the value itself moving, once.

**Why this is a migration and not `scripts/set_courier_promise.py`**, which is
what it was. That script existed for exactly one deploy and then had to be
remembered — and until somebody ran it, checkout kept quoting an hour for a
delivery that takes ninety minutes. A number the shop asked to change should
change when the deploy lands, not when someone opens a shell. The script is
deleted in the same commit; the screen is the way to change this from now on.

**Guarded so it never fights the screen.** It writes 90 only where the value is
still the 60 seeded by `088`. Once anybody edits the figure in the admin — to 75,
back to 60, to anything — this matches nothing and does nothing, including on an
environment restored from a dump taken before that edit. That is the failure
CLAUDE.md §7 warns content-in-migrations about, and a `WHERE` clause is the
whole of the fix.

Revision ID: 108_noon_send_90m
Revises: 107_premises_copy
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "108_noon_send_90m"
down_revision: Union[str, None] = "107_premises_copy"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEEDED_MINUTES = 60
AGREED_MINUTES = 90


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE couriers SET unbatched_promise_minutes = :new, updated_at = now() "
            "WHERE code = 'noon_send' AND unbatched_promise_minutes = :old"
        ),
        {"new": AGREED_MINUTES, "old": SEEDED_MINUTES},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE couriers SET unbatched_promise_minutes = :old, updated_at = now() "
            "WHERE code = 'noon_send' AND unbatched_promise_minutes = :new"
        ),
        {"new": AGREED_MINUTES, "old": SEEDED_MINUTES},
    )
