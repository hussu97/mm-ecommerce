"""The five lifecycle columns 099 did not reach.

Migration `099` constrained ten status columns to their Python enums and left
five behind — not by argument, just by not being on the list. Each of these is
an internal vocabulary of ours, the same as the ten:

* `devices.status` — the table already had a CHECK, on `platform`. `status` was
  simply never added to the same `__table_args__`.
* `tables.status` — `PosTable` had no `__table_args__` at all.
* `kitchen_ticket_items.status` — the parent `kitchen_tickets.status` was
  constrained by 099 and the child was not, which is the asymmetry that lets a
  ticket and its lines describe themselves in two vocabularies.
* `custom_orders.source` — the sibling `status` on the same table has been
  constrained since 099.
* `email_logs.status` — the vocabulary was a trailing comment on the column
  (`# sent | failed | skipped`), which is not a thing the database can check.
  It is now `EmailLogStatusEnum`.

Provider-verbatim columns stay unconstrained, per 099 and CLAUDE.md rule 6.
Nothing here records somebody else's words.

Values spelled out rather than imported, as in 099: a migration must say what
it did even after the code moves on.

No data repair, for 099's reason. A value outside these lists is a writer that
went off-script and worth looking at, not one to normalise away silently — if
`upgrade` fails, `SELECT DISTINCT <column>` on the named table says who.

Revision ID: 138_status_vocab_remainder
Revises: 137_order_fees
Create Date: 2026-08-23
"""

from typing import Sequence, Union

from alembic import op

revision: str = "138_status_vocab_remainder"
down_revision: Union[str, None] = "137_order_fees"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (table, column, allowed values, nullable) — mirrors `DeviceStatusEnum`,
#: `TableStatusEnum`, `KitchenTicketStatusEnum`, `CustomOrderSourceEnum` and
#: `EmailLogStatusEnum`. Adding a member to one of those enums means a
#: follow-up migration widening the matching CHECK.
VOCABULARIES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("devices", "status", ("available", "used", "disabled"), False),
    ("tables", "status", ("free", "occupied", "check_printed", "reserved"), False),
    (
        "kitchen_ticket_items",
        "status",
        ("new", "in_progress", "ready", "completed", "cancelled"),
        False,
    ),
    (
        "custom_orders",
        "source",
        ("website", "instagram", "whatsapp", "phone"),
        False,
    ),
    ("email_logs", "status", ("sent", "failed", "skipped"), False),
)


def _condition(column: str, values: tuple[str, ...], nullable: bool) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    condition = f"{column} IN ({allowed})"
    return f"{column} IS NULL OR {condition}" if nullable else condition


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
