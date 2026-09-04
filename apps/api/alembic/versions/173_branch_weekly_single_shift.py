"""Branch weekly hours: one shift per day.

`branch_weekly_hours` was scaffolded (migration 171) as a per-weekday, *multi-shift*
schedule for the marketplace fan-out. The unified branch-hours model makes the weekly
schedule the single source of truth for every "is the branch open" decision, and that
model is deliberately **one shift per day** — one open + close per weekday, a weekday
with no row = closed. Split shifts are not a real Melting Moments case and a second
shift on a day is a second answer to "when does it open".

So this collapses any existing multi-shift day into a single spanning shift
(earliest open, latest close) and swaps the uniqueness from
`(branch_id, weekday, shift_index)` to `(branch_id, weekday)` so the shape is enforced
by the database. `shift_index` is kept (always 0) rather than dropped — leaving the
column costs nothing and avoids churning every INSERT that still names it.

Structural backfill, not CMS content: it rewrites the machine-owned shift rows to the
new invariant. It is safe on a restored dump because the collapse is idempotent (a
schedule already one-per-day is left untouched) and the new UNIQUE would reject any
multi-shift row that slipped in.

Revision ID: 173_branch_weekly_single_shift
Revises: 172_agg_item_map_seed
Create Date: 2026-09-04
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "173_branch_weekly_single_shift"
down_revision: Union[str, None] = "172_agg_item_map_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Widen the surviving row of each day (the lowest shift_index — unique per
    #    day under the old constraint) to span that day's shifts, and reset it to
    #    index 0, so a collapsed day still covers the hours the split shifts did.
    op.execute(
        """
        UPDATE branch_weekly_hours k
        SET opens = a.opens, closes = a.closes, shift_index = 0
        FROM (
            SELECT branch_id, weekday,
                   MIN(opens) AS opens, MAX(closes) AS closes,
                   MIN(shift_index) AS keep_idx
            FROM branch_weekly_hours
            GROUP BY branch_id, weekday
        ) a
        WHERE k.branch_id = a.branch_id
          AND k.weekday = a.weekday
          AND k.shift_index = a.keep_idx
        """
    )
    # 2. Drop the now-redundant extra shifts — every survivor is index 0, and no
    #    two rows in a day shared an index, so anything else is an extra shift.
    op.execute("DELETE FROM branch_weekly_hours WHERE shift_index <> 0")
    # 3. Swap uniqueness to one row per (branch, weekday).
    op.drop_constraint(
        "uq_branch_weekly_hours_shift", "branch_weekly_hours", type_="unique"
    )
    op.create_unique_constraint(
        "uq_branch_weekly_hours_day", "branch_weekly_hours", ["branch_id", "weekday"]
    )


def downgrade() -> None:
    # The collapse is not reversible (the split shifts are gone); only the constraint
    # shape is restored so a re-upgrade lands cleanly.
    op.drop_constraint(
        "uq_branch_weekly_hours_day", "branch_weekly_hours", type_="unique"
    )
    op.create_unique_constraint(
        "uq_branch_weekly_hours_shift",
        "branch_weekly_hours",
        ["branch_id", "weekday", "shift_index"],
    )
