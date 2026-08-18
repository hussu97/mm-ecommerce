"""Branch holidays, and a configurable day count for a next-day courier.

Two schema changes serving one thing: the delivery promise reading numbers a
manager can change, instead of numbers a deploy can change.

**`branch_holidays`** — whole days a branch does not trade. Exceptions only:
the shop works seven days a week, so there is no weekday rule to store and no
row means open. Whole days by design, with no hour columns — a branch already
has `opening_from`/`opening_to` for the hours it trades, and a holiday with its
own hours would be a second answer to that question. Dated as `YYYY-MM-DD` text
with a format CHECK, matching `branch_business_days.business_date`, which is the
column it sits next to and is read alongside.

**`couriers.unbatched_promise_days`** — how many days after handover a
next-day courier delivers. It was the literal `1` in rule 3 of
`services/delivery_promise.py`, and "next day" is a partner's current SLA rather
than a law: a courier covering Al Ain quotes two. Defaults to 1, so every
existing row keeps promising exactly what it promised before this ran.

Schema only. noon Send's own 60 -> 90 move is a content edit and lives in
`scripts/set_courier_promise.py`, per CLAUDE.md §7 — and is one field on the
admin's new Estimates screen besides.

Revision ID: 106_branch_holidays
Revises: 105_consolidate_role_perms
Create Date: 2026-08-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "106_branch_holidays"
down_revision: Union[str, None] = "105_consolidate_role_perms"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "branch_holidays",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "branch_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("holiday_date", sa.String(10), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("branch_id", "holiday_date", name="uq_branch_holiday_date"),
        # These strings are compared against `date.isoformat()` output. A row
        # of any other shape would never match anything and would close the
        # branch on no day at all, silently.
        sa.CheckConstraint(
            r"holiday_date ~ '^\d{4}-\d{2}-\d{2}$'",
            name="ck_branch_holidays_holiday_date_format",
        ),
    )
    op.create_index("ix_branch_holidays_branch_id", "branch_holidays", ["branch_id"])
    # The promise asks for one branch's holidays from today onward, every time
    # a customer is quoted a delivery time.
    op.create_index(
        "ix_branch_holidays_holiday_date", "branch_holidays", ["holiday_date"]
    )

    op.add_column(
        "couriers",
        sa.Column(
            "unbatched_promise_days",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    op.drop_column("couriers", "unbatched_promise_days")
    op.drop_index("ix_branch_holidays_holiday_date", table_name="branch_holidays")
    op.drop_index("ix_branch_holidays_branch_id", table_name="branch_holidays")
    op.drop_table("branch_holidays")
