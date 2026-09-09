"""One sequence number per order's kitchen tickets (F-POS-31).

`send_to_kitchen` numbers each new ticket `count(tickets for this order) + 1`.
Two fires of the same check racing — a waiter double-tapping "send", or a
course fired from two stations at once — both read the same count and both
write the same `sequence`. Nothing stopped them: the KDS then shows two "#2"
tickets for one order, and a bump on one is ambiguous.

This adds the constraint that makes the race a caught error instead of silent
corruption: `(order_id, sequence)` is unique. The service now fires inside a
savepoint and, on the unique violation, recomputes the next number and retries
— so a concurrent send is serialised by the database rather than by hope.

Prod at write time held 825 tickets, all 825 `(order_id, sequence)` pairs
distinct, so the constraint applies with nothing to backfill.

Revision ID: 219_kitchen_ticket_seq_unique
Revises: 218_lotus_launch_seed
Create Date: 2026-09-09
"""

from typing import Sequence, Union

from alembic import op

revision: str = "219_kitchen_ticket_seq_unique"
down_revision: Union[str, None] = "218_lotus_launch_seed"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_kitchen_tickets_order_sequence",
        "kitchen_tickets",
        ["order_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_kitchen_tickets_order_sequence",
        "kitchen_tickets",
        type_="unique",
    )
