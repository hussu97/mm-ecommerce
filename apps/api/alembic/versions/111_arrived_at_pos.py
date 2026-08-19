"""Add arrived_at_pos to orderstatusenum, and orders.arrives_at

`confirmed` used to mean two things at once: the money arrived, and the shop was
told. For a batched zone those are hours apart, so the second one gets a status.

Revision ID: 111
Revises: 110
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa

revision = "111_arrived_at_pos"
down_revision = "110_publish_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `ALTER TYPE ... ADD VALUE` cannot be used by a statement in the same
    # transaction that added it, and the backfill below is exactly that. Alembic
    # wraps a migration in one transaction, so the value is committed on its own
    # first — the same shape migration 022 used for `payment_failed`.
    op.execute("COMMIT")
    op.execute(
        "ALTER TYPE orderstatusenum ADD VALUE IF NOT EXISTS 'arrived_at_pos' "
        "AFTER 'confirmed'"
    )
    op.execute("BEGIN")

    op.add_column(
        "orders",
        sa.Column("arrives_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_orders_arrives_at", "orders", ["arrives_at"])

    # ── the in-flight orders ──────────────────────────────────────────────────
    #
    # Every order already on a register is, by the new vocabulary, one that has
    # arrived — `check_number` is the fact, because nothing but
    # `attach_online_order` sets it and setting it is what put the order on the
    # counter. Leaving these at `confirmed` would hand the arrival sweep a
    # backlog of orders to announce a second time: a re-print at every terminal
    # in the branch and, on a zone we dispatch ourselves, a second van.
    #
    # Their `arrives_at` is stamped from the moment they actually landed, which
    # `order_status_events` still holds. Nothing reads it — the sweep only looks
    # at orders still `confirmed` — but an order that arrived with no arrival
    # time on it is a row that cannot be reported on.
    #
    # An order already `packed` or further keeps a null, deliberately. It is
    # past the question, and the only honest answer to "when would the system
    # have said this arrived" for an order that predates the concept is that
    # there is not one. Inventing a stamp from its confirmation would put a
    # number in a column that means something it does not.
    op.execute(
        """
        UPDATE orders o
           SET status = 'arrived_at_pos',
               arrives_at = COALESCE(
                   (SELECT e.at
                      FROM order_status_events e
                     WHERE e.order_id = o.id
                       AND e.status = 'confirmed'
                     ORDER BY e.at DESC
                     LIMIT 1),
                   o.opened_at,
                   o.created_at
               )
         WHERE o.status = 'confirmed'
           AND o.check_number IS NOT NULL
        """
    )

    # A confirmed order that never reached a register has not arrived, and the
    # sweep will land it on the next tick. Dating it now rather than leaving it
    # null keeps the sweep's null-means-overdue branch for genuine accidents.
    op.execute(
        """
        UPDATE orders
           SET arrives_at = COALESCE(opened_at, created_at)
         WHERE status = 'confirmed'
           AND arrives_at IS NULL
        """
    )


def downgrade() -> None:
    # PostgreSQL cannot drop an enum value without recreating the type, so the
    # orders that moved are walked back to `confirmed` instead. They keep their
    # check numbers, so the code this migration precedes reads them as published
    # exactly as it did before.
    op.execute("UPDATE orders SET status = 'confirmed' WHERE status = 'arrived_at_pos'")
    op.drop_index("ix_orders_arrives_at", table_name="orders")
    op.drop_column("orders", "arrives_at")
