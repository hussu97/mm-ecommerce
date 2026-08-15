"""A status the code never wrote may not be written.

Beyond the three native PG enums, every lifecycle status column is a bare
string. The Python enums are the vocabulary, but nothing held the database to
it: a typo'd status — `'colsed'`, `'CANCELLED'` where the code says
`'cancelled'` — persists silently and then disappears from every
`WHERE status = ...` that feeds a register, a board, or a Z-report. The row is
not wrong so much as *gone*: no list shows it, no sweep retries it, and the
first anyone hears is a customer on the phone.

String + CHECK rather than a native enum, on purpose: adding a value to a CHECK
is one cheap `ALTER TABLE`, while `ALTER TYPE ... ADD VALUE` has transactional
sharp edges and no way back. The allowed values are spelled out below rather
than imported from the enums, because a migration must say what it did even
after the code moves on.

Only internal lifecycles are constrained. Provider-verbatim columns —
`order_deliveries.courier_status`, `delivery_batches.courier_status`, webhook
payload fields — record somebody else's vocabulary and are unconstrained by
design: Lalamove is allowed to invent a status without breaking our ability to
record it. The models carry the same note.

No data repair. A fresh database has no rows to offend, and a production value
outside these lists would be a bug worth looking at, not one to normalise away
silently — if `upgrade` fails here, `SELECT DISTINCT <column>` on the named
table tells you exactly which writer went off-script.

Revision ID: 099_status_vocabulary_checks
Revises: 098_no_cancelled_open_checks
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "099_status_vocabulary_checks"
down_revision: Union[str, None] = "098_no_cancelled_open_checks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (table, column, allowed values, nullable) — values mirror the Python enums
#: at the time of writing: `PosOrderStatusEnum`, `OrderItemStatusEnum`,
#: `PaymentTransactionStatusEnum`, `CustomOrderStatusEnum`, `BatchStatusEnum`,
#: `TillStatusEnum`, `TransactionStatusEnum`, `PurchaseOrderStatusEnum`,
#: `KitchenTicketStatusEnum`, `TransferOrderStatusEnum`. Adding a member to an
#: enum means a follow-up migration widening the matching CHECK.
VOCABULARIES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    (
        "orders",
        "pos_status",
        (
            "draft",
            "pending",
            "active",
            "closed",
            "declined",
            "void",
            "returned",
            "joined",
        ),
        True,  # NULL for every storefront order that never reached a register
    ),
    (
        "order_items",
        "status",
        ("pending", "active", "closed", "moved", "void", "returned", "declined"),
        True,  # NULL on storefront lines, same as the order's pos_status
    ),
    (
        "payment_transactions",
        "status",
        ("pending", "succeeded", "failed", "cancelled", "refunded", "disputed"),
        False,
    ),
    (
        "custom_orders",
        "status",
        ("enquiry", "confirmed", "in_production", "ready", "completed", "cancelled"),
        False,
    ),
    (
        "delivery_batches",
        "status",
        ("pending", "dispatching", "dispatched", "failed", "cancelled"),
        False,
    ),
    ("tills", "status", ("open", "closed"), False),
    (
        "inventory_transactions",
        "status",
        ("draft", "pending", "declined", "closed"),
        False,
    ),
    (
        "purchase_orders",
        "status",
        ("draft", "pending", "approved", "declined", "partially_received", "closed"),
        False,
    ),
    (
        "kitchen_tickets",
        "status",
        ("new", "in_progress", "ready", "completed", "cancelled"),
        False,
    ),
    (
        "transfer_orders",
        "status",
        ("draft", "pending", "accepted", "declined", "closed"),
        False,
    ),
)


def _condition(column: str, values: tuple[str, ...], nullable: bool) -> str:
    allowed = ", ".join(f"'{v}'" for v in values)
    condition = f"{column} IN ({allowed})"
    if nullable:
        # `NULL IN (...)` already satisfies a CHECK, but saying it keeps the
        # constraint readable as the rule it is: absent is fine, misspelt is not.
        condition = f"{column} IS NULL OR {condition}"
    return condition


def upgrade() -> None:
    for table, column, values, nullable in VOCABULARIES:
        op.create_check_constraint(
            f"ck_{table}_{column}_allowed",
            table,
            _condition(column, values, nullable),
        )


def downgrade() -> None:
    for table, column, _values, _nullable in reversed(VOCABULARIES):
        op.drop_constraint(f"ck_{table}_{column}_allowed", table, type_="check")
