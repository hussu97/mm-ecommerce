"""Remove gift cards, loyalty and house accounts.

All three were built end to end — models, append-only ledgers, services,
routers, permissions — and never wired into a single selling path. No order ever
earned a loyalty point: `earn_loyalty` has no caller anywhere. No payment ever
drew down a gift card: `redeem_gift_card` has no caller either, and
`record_payment` never branched on the tender type at all.

That last part is why this is a removal rather than a deprecation. `gift_card`
and `house_account` were selectable tender types in the console, and picking one
settled the entire check against nothing — the money was simply written off, and
the ledger that was supposed to record it was never touched. It was a hole
shaped like a feature, waiting for somebody to choose it from a dropdown.

Nothing outside these tables references them: every foreign key is outbound (to
`users` and `orders`) or between the six tables themselves, so they drop cleanly
in dependency order.

`payment_methods.type` is a plain `String(30)`, not a native enum, so narrowing
the Python enum needs no `ALTER TYPE`. Any row still carrying one of the retired
types is deactivated rather than deleted — `order_payments.payment_method_id` is
ON DELETE RESTRICT, and a historical payment must keep naming the tender it was
taken on. The register decodes an unrecognised type as `.other` rather than
throwing, so a surviving row cannot break the tender list.

The data is not preserved on downgrade. Recreating empty tables is what a
downgrade can honestly offer here; there were no balances to lose.

Revision ID: 079_drop_unused_marketing
Revises: 078_payment_idempotency_key
Create Date: 2026-08-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "079_drop_unused_marketing"
down_revision: Union[str, None] = "078_payment_idempotency_key"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Retire any tender the console could still offer. Deactivated, not deleted:
    # order_payments references payment_methods with ON DELETE RESTRICT, and a
    # past payment has to keep naming what it was taken on.
    op.execute(
        """
        UPDATE payment_methods
           SET is_active = false,
               deleted_at = COALESCE(deleted_at, now())
         WHERE type IN ('gift_card', 'house_account')
        """
    )

    # Children first — each references its parent with ON DELETE CASCADE, but
    # dropping in order keeps this readable and independent of that.
    op.drop_table("house_account_transactions")
    op.drop_table("house_accounts")
    op.drop_table("loyalty_transactions")
    op.drop_table("loyalty_programs")
    op.drop_table("gift_card_transactions")
    op.drop_table("gift_cards")


def downgrade() -> None:
    op.create_table(
        "gift_cards",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(30), nullable=False, unique=True),
        sa.Column("initial_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "issued_by_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_gift_cards_code", "gift_cards", ["code"])
    op.create_index("ix_gift_cards_status", "gift_cards", ["status"])

    op.create_table(
        "gift_card_transactions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "gift_card_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("gift_cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "loyalty_programs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("points_per_currency", sa.Numeric(10, 4), nullable=False),
        sa.Column("currency_per_point", sa.Numeric(10, 4), nullable=False),
        sa.Column("minimum_redemption_points", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "loyalty_transactions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_loyalty_transactions_customer_id",
        "loyalty_transactions",
        ["customer_id"],
    )

    op.create_table(
        "house_accounts",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("credit_limit", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("customer_id", name="uq_house_account_customer"),
    )

    op.create_table(
        "house_account_transactions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "house_account_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("house_accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
