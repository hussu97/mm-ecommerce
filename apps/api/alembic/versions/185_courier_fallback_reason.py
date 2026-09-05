"""Record why an automatic dispatch fell back off its zone's courier.

`order_deliveries.fallback_reason` is the durable copy of the line
`courier_service._dispatch_once` already logs when the automatic dispatcher
cannot use the courier a zone chose and books another — Slider's wallet
returning 402, noon Send out of range, a booking that timed out. It is
deliberately separate from `last_error`: the fallback booking *succeeded*, so
recording it there would put every fallback on the admin's needs-a-human list.

It exists because that fallback was silent. A Slider account whose prepaid
wallet could not cover a real fare returned 402 on every booking, every order
dropped to Lalamove, `last_error` was cleared by the successful Lalamove
booking, and nothing on the row or on any dashboard said Slider had stopped
carrying anything. This column plus the fingerprinted Sentry warning
`_dispatch_once` now raises are what make that state visible.

Nullable and unconstrained free text, like `last_error`: it is a diagnostic
sentence, not a lifecycle value. A plain nullable ADD COLUMN, no table rewrite.

Revision ID: 185_courier_fallback_reason
Revises: 184_drop_branch_opening_window
Create Date: 2026-09-05
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "185_courier_fallback_reason"
down_revision: Union[str, None] = "184_drop_branch_opening_window"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order_deliveries",
        sa.Column("fallback_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_deliveries", "fallback_reason")
