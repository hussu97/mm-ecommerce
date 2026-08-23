"""An order can finally say when it reached each status, not just when it was written.

The admin's timeline showed one timestamp — `created` — and four blanks. Not
because the code picking them was wrong, but because there was nothing to pick:
`confirmed` had no source at all, and `packed`, `out_for_delivery` and
`delivered` all read columns on `order_deliveries` that only a courier webhook
ever fills. A pickup order, a third-party zone or an order somebody walked
through by hand produces none of them.

This table records the transitions themselves. Going forward they are captured
by the listener in `app/models/order_status_event.py`, which cannot be bypassed
by assigning `Order.status` — the thing eleven of the thirteen writers do.

**The backfill is the load-bearing half of this migration.** Without it every
order already in the database keeps showing one stamp, and the fix reads as
broken to the person who reported it. Four sources, in descending order of how
directly they witnessed the transition:

1. `orders.created_at` — every order gets a `created` row. This is the only one
   that is certain rather than reconstructed.
2. `order_deliveries.dispatchable_at` / `picked_up_at` / `delivered_at` — real
   stamps written at the moment they describe, for the orders that have them.
3. `audit_logs` rows where an admin moved the status. `changes->>'to'` is the
   destination and `created_at` is when. Only the admin endpoint writes these,
   so they cover a minority of orders, but where they exist they are exact.
4. `payment_transactions` — a succeeded attempt is when an order became
   `confirmed`. `updated_at` rather than `created_at`: the row is created when
   the session opens, which is before the customer has paid anything.

Everything written here is `source = 'backfill'` so a reconstruction is never
mistaken for a contemporaneous record. Deliberately absent: anything derived
from `orders.updated_at`. That column moves every time anybody edits an order,
so it would put a confident and wrong timestamp under `Confirmed` on every row —
worse than the blank it replaces.

Revision ID: 091_order_status_events
Revises: 090_webhook_logs_for_payments
Create Date: 2026-08-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "091_order_status_events"
down_revision: Union[str, None] = "090_webhook_logs_for_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Who the zone originally chose, for the orders an admin later moves onto a
    # different courier. `provider` is the live answer and everything dispatches
    # off it; without this, flipping it makes a reassigned order look like one
    # that was always Lalamove — and the two want different promises kept.
    op.add_column(
        "order_deliveries",
        sa.Column("original_provider", sa.String(20), nullable=True),
    )

    op.create_table(
        "order_status_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False, server_default="system"),
        sa.Column(
            "actor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("actor_label", sa.String(255), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_order_status_events_order_id", "order_status_events", ["order_id"]
    )
    op.create_index(
        "ix_order_status_events_order_at", "order_status_events", ["order_id", "at"]
    )

    # ── backfill ─────────────────────────────────────────────────────────────
    #
    # One statement per source, each an INSERT … SELECT so the whole thing stays
    # in the database rather than pulling every order through Python. The
    # `NOT EXISTS` guard on each makes the set idempotent: a rerun, or two
    # sources agreeing about the same transition, produces one row rather than
    # two. `gen_random_uuid()` is pgcrypto, available in Postgres 13+ core.

    # 1. Every order was created.
    op.execute(
        """
        INSERT INTO order_status_events (id, order_id, status, at, source)
        SELECT gen_random_uuid(), o.id, 'created', o.created_at, 'backfill'
        FROM orders o
        """
    )

    # 2. The courier's own stamps, which were written at the moment they
    #    describe and are the most trustworthy thing here after `created_at`.
    for column, status in (
        ("dispatchable_at", "packed"),
        ("picked_up_at", "out_for_delivery"),
        ("delivered_at", "delivered"),
    ):
        op.execute(
            f"""
            INSERT INTO order_status_events (id, order_id, status, at, source)
            SELECT gen_random_uuid(), d.order_id, '{status}', d.{column}, 'backfill'
            FROM order_deliveries d
            WHERE d.{column} IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM order_status_events e
                  WHERE e.order_id = d.order_id AND e.status = '{status}'
              )
            """
        )

    # 3. Admin-driven changes. `entity_id` holds the order *number*, not the id,
    #    which is why this joins on `order_number`. Distinct on (order, status)
    #    keeps the earliest, matching the "when did it first reach this" reading
    #    the timeline wants.
    op.execute(
        """
        INSERT INTO order_status_events
            (id, order_id, status, at, source, actor_id, actor_label)
        SELECT gen_random_uuid(), x.order_id, x.status, x.at, 'backfill',
               x.admin_id, x.admin_email
        FROM (
            SELECT DISTINCT ON (o.id, a.changes->>'to')
                   o.id           AS order_id,
                   a.changes->>'to' AS status,
                   a.created_at   AS at,
                   a.admin_id     AS admin_id,
                   a.admin_email  AS admin_email
            FROM audit_logs a
            JOIN orders o ON o.order_number = a.entity_id
            WHERE a.action = 'STATUS_CHANGE'
              AND a.entity_type = 'order'
              AND a.changes->>'to' IS NOT NULL
            ORDER BY o.id, a.changes->>'to', a.created_at
        ) x
        WHERE NOT EXISTS (
            SELECT 1 FROM order_status_events e
            WHERE e.order_id = x.order_id AND e.status = x.status
        )
        """
    )

    # 4. Payment. A succeeded attempt is the moment the order became confirmed;
    #    `updated_at` because the row exists from when the session opened, which
    #    is before any money moved.
    op.execute(
        """
        INSERT INTO order_status_events (id, order_id, status, at, source, note)
        SELECT gen_random_uuid(), x.order_id, 'confirmed', x.at, 'backfill', x.gateway
        FROM (
            SELECT DISTINCT ON (t.order_id)
                   t.order_id, t.updated_at AS at, t.gateway
            FROM payment_transactions t
            WHERE t.status = 'succeeded'
            ORDER BY t.order_id, t.updated_at
        ) x
        WHERE NOT EXISTS (
            SELECT 1 FROM order_status_events e
            WHERE e.order_id = x.order_id AND e.status = 'confirmed'
        )
        """
    )

    # 5. Anything the order is sitting in *now* that none of the above
    #    witnessed — a status reached by a writer that leaves no trace, which is
    #    most of them. There is no honest moment for these, so they take the
    #    order's `updated_at` and say so in `note`. Without this a delivered
    #    third-party order would show `created` and nothing else, which is the
    #    exact complaint. With it, the last step carries a stamp that is flagged
    #    as approximate rather than presented as a record.
    op.execute(
        """
        INSERT INTO order_status_events (id, order_id, status, at, source, note)
        SELECT gen_random_uuid(), o.id, o.status::text, o.updated_at, 'backfill',
               'approximate: reconstructed from the order''s last edit'
        FROM orders o
        WHERE NOT EXISTS (
            SELECT 1 FROM order_status_events e
            WHERE e.order_id = o.id AND e.status = o.status::text
        )
        """
    )

    # ── and now the copies go ─────────────────────────────────────────────────
    #
    # `picked_up_at` and `delivered_at` held exactly the two moments the rows
    # above now hold as `out_for_delivery` and `delivered`. Two tables holding
    # one fact is two chances to disagree, and these two were read by different
    # screens — the customer's timeline from one, the admin's delivery card from
    # the other — so a disagreement would have shown up as two answers to the
    # same question rather than as an error.
    #
    # Dropped only after step 2 above has copied them across. Both are restored
    # from the history on the way down.
    #
    # What stays on `order_deliveries`, because none of it is an order status:
    #   `booked_at`        — when the courier accepted the job
    #   `cancelled_at`     — when the *booking* was called off, which is not the
    #                        order being cancelled and routinely happens while
    #                        the order carries on
    #   `dispatchable_at`  — a scheduling input `batching_service` matches
    #                        windows against, not a record of a transition
    #   `status_updated_at`— the out-of-order guard for their webhooks
    op.drop_column("order_deliveries", "picked_up_at")
    op.drop_column("order_deliveries", "delivered_at")


def downgrade() -> None:
    # Put the two columns back and refill them from the history, so going down
    # loses nothing an older revision would have had. The history is the
    # authority in both directions; this just projects it back into the shape
    # the previous revision read.
    op.add_column(
        "order_deliveries",
        sa.Column("picked_up_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_deliveries",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column, status in (
        ("picked_up_at", "out_for_delivery"),
        ("delivered_at", "delivered"),
    ):
        op.execute(
            f"""
            UPDATE order_deliveries d
            SET {column} = e.at
            FROM (
                SELECT DISTINCT ON (order_id) order_id, at
                FROM order_status_events
                WHERE status = '{status}'
                ORDER BY order_id, at DESC
            ) e
            WHERE e.order_id = d.order_id
            """
        )

    op.drop_index("ix_order_status_events_order_at", table_name="order_status_events")
    op.drop_index("ix_order_status_events_order_id", table_name="order_status_events")
    op.drop_table("order_status_events")
    op.drop_column("order_deliveries", "original_provider")
