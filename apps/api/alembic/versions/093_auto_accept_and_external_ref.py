"""A terminal can take website orders on its own, and an order can carry someone else's reference.

Two unrelated columns, in one migration because each is a single nullable-ish
add and neither is worth its own revision.

`devices.auto_accept_online_orders` — the Sharjah kitchen has no counter staff
watching the iPad. A website order lands, the alarm rings, and it waits for
somebody to walk over and press Accept before anything reaches the printer. For
a kitchen-only site that press carries no information: there is nobody deciding
whether to take the order, only somebody being interrupted to confirm one that
was already paid for. This flag lets that terminal accept and print by itself.

It sits beside `receives_online_orders` and is deliberately a second column
rather than a third state of the first. They answer different questions —
*should this terminal be shown the branch's online orders* and *should it wait
for a human before taking one* — and a counter iPad that shows orders and waits
is the normal, correct combination the shop runs today.

Per device, not per branch: a branch can have a counter iPad where a cashier
should see the order and a kitchen iPad that should just print it, and the same
branch wants both behaviours at once.

`orders.external_reference` — whoever else's number this order is also known by.
Nothing writes it yet; it exists so the receipt can print an aggregator's or a
marketplace's own reference when an order carries one, the way the shop's
current tickets carry a Talabat number. Deliberately a free-text column and not
a foreign key to anything: it is a string somebody else owns, and its only job
is to be read back to them over a phone.

Revision ID: 093_auto_accept_external
Revises: 092_cart_addons_personalisation
Create Date: 2026-08-15
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "093_auto_accept_external"
down_revision: Union[str, None] = "092_cart_addons_personalisation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "devices",
        sa.Column(
            "auto_accept_online_orders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # Off for every existing terminal. A flag that silently changed how a live
    # till behaved on deploy is not a flag anybody asked for — the shop turns
    # this on per device, in the console, when it decides to.

    op.add_column(
        "orders",
        sa.Column("external_reference", sa.String(120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("orders", "external_reference")
    op.drop_column("devices", "auto_accept_online_orders")
