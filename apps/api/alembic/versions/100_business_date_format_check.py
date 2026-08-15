"""A business date is ten characters of YYYY-MM-DD or it is not a date.

`business_date` is `String(10)` in seven tables while every sibling date column
is a real `Date`. The string is load-bearing: check numbers reset on it, tills
open and close on it, Z-reports group by it, and the register's day is joined
to the shop's day through exact string equality. `'2026-8-5'`, `'05-08-2026'`
or a stray space would not error anywhere — it would quietly open a second
business day next to the real one and split the till across both.

The honest fix is converting the column to `Date`, which is deferred: seven
tables, every reader, and the register's wire format all move together. Until
then this CHECK pins the one thing the string format relies on, so a writer
that bypasses `business_day_service` cannot mint a day that sorts wrong.

Format only — `'2026-99-99'` still passes. The regexp keeps the constraint
cheap and the migration honest about what it guarantees: shape, not calendar
validity. NULL passes (`NULL ~ ...` is NULL, which satisfies a CHECK); only
`orders.business_date` is nullable and NULL there means "never reached a
register", which is not a formatting problem.

Revision ID: 100_business_date_format_check
Revises: 099_status_vocabulary_checks
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "100_business_date_format_check"
down_revision: Union[str, None] = "099_status_vocabulary_checks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES: tuple[str, ...] = (
    "orders",
    "order_payments",
    "branch_business_days",
    "tills",
    "inventory_transactions",
    "purchase_orders",
    "transfer_orders",
)

CONDITION = r"business_date ~ '^\d{4}-\d{2}-\d{2}$'"


def upgrade() -> None:
    for table in TABLES:
        op.create_check_constraint(f"ck_{table}_business_date_format", table, CONDITION)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_constraint(f"ck_{table}_business_date_format", table, type_="check")
