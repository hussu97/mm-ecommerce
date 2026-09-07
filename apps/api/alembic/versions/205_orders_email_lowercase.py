"""Fold every existing `orders.email` to lower case.

`orders.email` is what every ownership check keys on — the confirmation page,
the track page, the new-customer coupon — and each compares a lower-cased value.
The column was written verbatim, so an order placed as `John@x.com` never matched
`john@x.com`, and its own customer was locked out of confirmation and tracking.

Creation now normalises on write (`OrderCreate._normalise_email` and
`_persist_order`), and the reads compare with `func.lower(...)`. This carries the
rows already in the table to the same canonical form, so a lookup no longer has
to lower-case the column at query time to find them.

Guarded and idempotent: it touches only rows that are not already lower-cased
(`WHERE email <> lower(email)`), so a re-run — or a run over a restored dump —
changes exactly the rows still holding a capital and nothing else. The unique
index on the column is only ever tightened by folding case together, and today
no two rows differ by case alone.

Irreversible by nature: the original capitalisation is not recorded anywhere, so
`downgrade` cannot restore it and does not pretend to.

Revision ID: 205_orders_email_lowercase
Revises: 204_agg_subtotal_inclusive
Create Date: 2026-09-07
"""

from typing import Sequence, Union

from alembic import op

revision: str = "205_orders_email_lowercase"
down_revision: Union[str, None] = "204_agg_subtotal_inclusive"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    result = op.execute(
        "UPDATE orders SET email = lower(email) WHERE email <> lower(email)"
    )
    # Alembic's execute returns a CursorResult under the sync driver used for
    # migrations; log the count where it is available.
    rowcount = getattr(result, "rowcount", None)
    if rowcount is not None:
        print(f"205: lower-cased {rowcount} order email(s)")


def downgrade() -> None:
    # The original case is not stored anywhere, so it cannot be restored. Folding
    # is one-way; there is nothing to undo.
    pass
