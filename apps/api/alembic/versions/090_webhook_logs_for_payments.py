"""Payment webhooks join the audit log, and its id column stops saying "courier".

`webhook_logs` was built for the couriers, and it is the table that answers the
question the couriers made unanswerable: what actually arrived, as opposed to
what we accepted. Payment webhooks need that answer more, not less. A courier
push we drop loses tracking; a payment event we drop is money that moved with
the order none the wiser, and until now the only evidence of one was container
stdout, which a restart takes.

The rename is the whole schema change. `courier_order_id` holds "their id for
the thing this push is about" — a Lalamove booking, a noon Send task, and now a
Stripe payment intent or a Ziina one. Writing `pi_3RxK…` into a column called
`courier_order_id` is exactly the conflation the gateway split just removed from
`orders`, and this table is purged on a retention window, so a rename here costs
nothing and buys a column that means what it says.

`order_deliveries.courier_order_id` and `delivery_batches.courier_order_id` keep
their names. Those genuinely are courier bookings and always will be.

Revision ID: 090_webhook_logs_for_payments
Revises: 089_payment_gateways
Create Date: 2026-08-08
"""

from typing import Sequence, Union

from alembic import op

revision: str = "090_webhook_logs_for_payments"
down_revision: Union[str, None] = "089_payment_gateways"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("webhook_logs", "courier_order_id", new_column_name="external_id")
    # Postgres carries the index across a column rename but keeps its old name,
    # which leaves `ix_webhook_logs_courier_order_id` sitting on a column that
    # no longer exists by that name — readable only to whoever remembers this
    # migration.
    op.execute(
        "ALTER INDEX IF EXISTS ix_webhook_logs_courier_order_id "
        "RENAME TO ix_webhook_logs_external_id"
    )


def downgrade() -> None:
    op.execute(
        "ALTER INDEX IF EXISTS ix_webhook_logs_external_id "
        "RENAME TO ix_webhook_logs_courier_order_id"
    )
    op.alter_column("webhook_logs", "external_id", new_column_name="courier_order_id")
