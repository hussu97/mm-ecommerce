"""One open till per cashier, and one per device.

A till is opened read-then-insert (`till_service.open_till` checks for an existing
open till, then inserts a new row), with nothing in the schema to stop two of them
existing at once. Two terminals sharing an iPad — or one request the register
retried after a slow response — could each read "no open till" and both insert,
splitting a cashier's takings across two reconciliations that each look complete.

The fix is two PARTIAL unique indexes: at most one `open` till per `user_id`, and
at most one per `device_id` (only where a device is set — a till can be opened
without one). The companion code change catches the `IntegrityError` the loser of
a race now gets and returns the winning till, so an open stays idempotent rather
than turning into a 500.

Additive and safe to build online: production carries 0 overlapping open tills
(no `user_id` and no non-null `device_id` has two `status='open'` rows), so both
indexes create clean.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "202_till_open_uniqueness"
down_revision: Union[str, None] = "201_report_input_drop_production"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USER_IDX = "uq_tills_open_per_user"
_DEVICE_IDX = "uq_tills_open_per_device"


def upgrade() -> None:
    op.create_index(
        _USER_IDX,
        "tills",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )
    op.create_index(
        _DEVICE_IDX,
        "tills",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND device_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_DEVICE_IDX, table_name="tills")
    op.drop_index(_USER_IDX, table_name="tills")
