"""A normalised, customer-shown reason for a failed payment attempt.

`payment_transactions` already kept the gateway's raw `error_code`/`error_message`,
but the raw code is a bank-and-reconciliation word (~45 of them from Stripe
alone) and the storefront had nothing it could safely turn into one honest
sentence. This adds the bucket the customer is actually shown — the same
string-plus-CHECK pattern as `status` next door, mirroring
`PaymentFailureReason` in `app/models/payment_transaction.py`.

Nullable, and no backfill: most attempts never failed, a gateway with no
decline taxonomy (Ziina) legitimately leaves it null, and past failures kept
only the raw code we cannot re-map here without the code's context. New failures
fill it going forward. Adding a member to the enum means widening this CHECK in
a follow-up migration.

Revision ID: 148_payment_failure_reason
Revises: 147_agg_line_total_backfill
Create Date: 2026-08-25
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "148_payment_failure_reason"
down_revision: Union[str, None] = "147_agg_line_total_backfill"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Mirrors `PaymentFailureReason` at the time of writing. Spelled out rather
#: than imported, because a migration must say what it did even after the code
#: moves on.
_REASONS = (
    "insufficient_funds",
    "expired_card",
    "incorrect_cvc",
    "incorrect_number",
    "incorrect_details",
    "card_not_supported",
    "authentication_required",
    "processing_error",
    "duplicate",
    "card_declined",
)

_CHECK = "ck_payment_transactions_failure_reason_allowed"


def upgrade() -> None:
    op.add_column(
        "payment_transactions",
        sa.Column("failure_reason", sa.String(length=40), nullable=True),
    )
    allowed = ", ".join(f"'{value}'" for value in _REASONS)
    op.create_check_constraint(
        _CHECK,
        "payment_transactions",
        f"failure_reason IS NULL OR failure_reason IN ({allowed})",
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, "payment_transactions", type_="check")
    op.drop_column("payment_transactions", "failure_reason")
