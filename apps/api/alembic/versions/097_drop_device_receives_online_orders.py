"""Which terminals see an online order is the branch's question, not the iPad's.

`devices.receives_online_orders` was added beside `auto_accept_online_orders` to
distinguish two things that genuinely are different questions — *show me the
branch's online orders* and *wait for a human before taking one*.

Nothing ever read the first one. No query filtered on it, no service consulted
it, and the word does not appear in a single line of Swift. It could be set in
the API and stored faithfully and it changed nothing: the register attaches its
incoming-order queue on being signed in with a branch, and nothing asks whether
this particular terminal was meant to receive.

The Sharjah kitchen terminal has run with it `false` throughout, taking and
auto-accepting online orders the whole time. That is the behaviour the shop
wants, so it never hurt — but the setting has been lying for as long as it has
existed, and the next person to toggle it would have believed it.

The shop's answer, asked directly: the **branch** decides whether it takes
online orders, and every terminal at that branch sees them. An order accepted on
one is accepted on all — each register re-reads the list on its own poll, and
the alarm on the others clears itself as soon as the order stops waiting.

`auto_accept_online_orders` stays. Who presses Accept really is a property of
the individual iPad: a counter terminal with somebody standing at it should keep
waiting for them, and the kitchen one with nobody watching should not.

`branches.receives_online_orders` is untouched, and is a different column on a
different table that two services genuinely query.

Revision ID: 097_drop_device_receives_online
Revises: 096_gateway_fees
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "097_drop_device_receives_online"
down_revision: Union[str, None] = "096_gateway_fees"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("devices", "receives_online_orders")


def downgrade() -> None:
    # Restored `false`, the value it shipped with — and, being unread, the value
    # every row effectively had whatever it said.
    op.add_column(
        "devices",
        sa.Column(
            "receives_online_orders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
