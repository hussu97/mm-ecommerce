"""Card payments become gateway-agnostic: Stripe *or* Ziina.

Three changes, and the third is only honest because of the first two.

**Gateways become a table.** Which processor settles a card was decided by an
import: a one-entry registry in `payment_service` and the literal string
`"stripe"` posted by the checkout page. A Stripe incident was therefore a
deploy, at the exact moment a deploy is hardest. `payment_gateways` is the same
shape as `couriers` and solves the same problem — a commercial relationship that
changes without the code changing should not be written in the code.

**Payment attempts become rows.** The order carried the whole story in
`payment_provider` + `payment_id`, both overwritten on every retry, and
paid-ness was inferred from an ID prefix: a Stripe session is `cs_…` and the
intent it becomes is `pi_…`, so `startswith("pi_")` stood in for "this order is
paid". Ziina mints its intent *up front*, so that same string is present on an
order nobody has paid for. The inference does not survive a second gateway, and
`payment_transactions` replaces it with a fact — plus the things a prefix never
carried: which gateway, how much, and why the attempt before it failed.

**Method stops meaning gateway.** `orders.payment_method` held `stripe` for
every card order, which conflated what the customer chose with who processed it.
It becomes `card`, and `payment_provider` keeps holding the processor. The
customer picked a card; they had no opinion about Stripe.

Ziina ships **inactive**, and `ZIINA_ENABLED` defaults false, and the admin
refuses to activate a gateway with no credentials. Production stays on Stripe
until three separate people-decisions say otherwise.

Revision ID: 089_payment_gateways
Revises: 088_batch_groups_and_couriers
Create Date: 2026-08-08
"""

from decimal import Decimal
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "089_payment_gateways"
down_revision: Union[str, None] = "088_batch_groups_and_couriers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (code, name, is_active, priority, min_amount, test_mode)
#:
#: Stripe is active because it is what production is already running. Ziina is
#: not, because this migration is the plumbing and not the decision — the whole
#: point of the table is that the decision is made later, by a person, in the
#: admin, and can be unmade the same way.
#:
#: Both floors are AED 2.00, which each processor enforces at its own edge where
#: the refusal is an opaque 400 on the last screen of checkout. Here it becomes
#: a sentence the customer can act on.
GATEWAYS = [
    ("stripe", "Stripe", True, 1, "2.00", False),
    ("ziina", "Ziina", False, 2, "2.00", True),
]


def upgrade() -> None:
    # ── payment_gateways ──────────────────────────────────────────────────
    op.create_table(
        "payment_gateways",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column(
            "supports_failover",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("min_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("max_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "test_mode", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_payment_gateways_code", "payment_gateways", ["code"], unique=True
    )

    for code, name, is_active, priority, min_amount, test_mode in GATEWAYS:
        op.execute(
            sa.text(
                """
                INSERT INTO payment_gateways
                    (code, name, is_active, priority, min_amount, test_mode)
                VALUES (:code, :name, :is_active, :priority, :min_amount, :test_mode)
                ON CONFLICT (code) DO NOTHING
                """
            ).bindparams(
                code=code,
                name=name,
                is_active=is_active,
                priority=priority,
                # A `Decimal`, not the string it is written as above. asyncpg
                # binds a `str` as `VARCHAR` and Postgres refuses to compare it
                # to `numeric` — the insert fails outright, so the failure is at
                # least loud, but it fails on the very first migration run.
                min_amount=Decimal(min_amount),
                test_mode=test_mode,
            )
        )

    # ── payment_transactions ──────────────────────────────────────────────
    op.create_table(
        "payment_transactions",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("gateway", sa.String(20), nullable=False),
        sa.Column("session_id", sa.String(255), nullable=True),
        sa.Column("payment_id", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("raw_status", sa.String(60), nullable=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False, server_default="AED"),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_payment_transactions_order_id", "payment_transactions", ["order_id"]
    )
    op.create_index(
        "ix_payment_transactions_gateway", "payment_transactions", ["gateway"]
    )
    op.create_index(
        "ix_payment_transactions_status", "payment_transactions", ["status"]
    )
    # Scoped by gateway, not global. Stripe and Ziina both mint IDs beginning
    # `pi_`, and a unique index on the bare string would eventually collide two
    # unrelated payments — which, on the lookup path that resolves a webhook to
    # an order, means confirming somebody else's cake.
    op.create_index(
        "uq_payment_transactions_gateway_session",
        "payment_transactions",
        ["gateway", "session_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.create_index(
        "uq_payment_transactions_gateway_payment",
        "payment_transactions",
        ["gateway", "payment_id"],
        unique=True,
        postgresql_where=sa.text("payment_id IS NOT NULL"),
    )

    # ── orders.payment_method: the customer's choice, not the processor ───
    #
    # Only `stripe` moves. `cod` already meant what it says, and the BNPL values
    # are genuinely methods — if a `tabby` row exists it is correct as it stands.
    #
    # `payment_provider` is deliberately untouched: it already holds `stripe` or
    # `cod` per order, which is exactly the gateway, so every existing row is
    # already right under the new reading and no backfill can improve it.
    op.execute(
        """
        UPDATE orders
           SET payment_method = 'card'
         WHERE payment_method = 'stripe'
        """
    )


def downgrade() -> None:
    # Put the conflation back, so a rollback leaves a database the previous
    # release can actually read: it decides cash-versus-card off this column and
    # does not know the word `card`.
    op.execute(
        """
        UPDATE orders
           SET payment_method = 'stripe'
         WHERE payment_method = 'card'
        """
    )
    op.drop_table("payment_transactions")
    op.drop_index("ix_payment_gateways_code", table_name="payment_gateways")
    op.drop_table("payment_gateways")
