"""Record every driver an order has had, and which one is current

`order_deliveries` held one driver and no memory of any other. A courier that
swaps a rider mid-booking — routine on both integrations — overwrote nothing,
because the fill only ran when the row had *no* driver at all: the shop kept
ringing the person who had already dropped the job.

`order_drivers` is one row per stint. `is_active` is TRUE on the current one and
NULL on every past one, under `UNIQUE (order_id, is_active)` — Postgres counts
NULLs in a unique index as distinct, so any number of finished stints coexist
while a second live one is refused by the database. A CHECK keeps FALSE out,
which would otherwise collide with every other finished row.

The uniqueness is `(order_id, is_active)` and not `(order_id, driver, is_active)`
on purpose: with the driver in the key, two *different* people could both be
active on one order, which is the thing the constraint exists to prevent.

`order_deliveries` keeps its driver columns as a live copy of the active row —
one writer, `driver_assignment.record`, same transaction — so the register's
order list and the fifteen-second tracking webhook need no join to say who is
coming. Three columns are added there for what the single row could never hold:
when this driver took it, how many drivers there have been, and when the
position on the row was actually true.

Revision ID: 112
Revises: 111
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

revision = "112_driver_reassign"
down_revision = "111_arrived_at_pos"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_drivers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("courier_order_id", sa.String(64), nullable=True),
        sa.Column("courier_driver_id", sa.String(64), nullable=True),
        sa.Column("name", sa.String(150), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("plate", sa.String(30), nullable=True),
        sa.Column("latitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("longitude", sa.Numeric(9, 6), nullable=True),
        sa.Column("located_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True),
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
        sa.UniqueConstraint("order_id", "is_active", name="uq_order_driver_active"),
        sa.CheckConstraint(
            "is_active IS NULL OR is_active IS TRUE",
            name="ck_order_drivers_active_true_or_null",
        ),
    )
    op.create_index("ix_order_drivers_order_id", "order_drivers", ["order_id"])
    op.create_index(
        "ix_order_drivers_order_sequence", "order_drivers", ["order_id", "sequence"]
    )

    op.add_column(
        "order_deliveries",
        sa.Column(
            "driver_assignment_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "order_deliveries",
        sa.Column("driver_assigned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "order_deliveries",
        sa.Column("driver_location_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── the bookings already carrying a driver ────────────────────────────────
    #
    # Each gets the stint it has plainly had. `booked_at` is the honest stamp
    # for when that driver took it: it is the only moment on the row that is
    # certainly not after the assignment, and `updated_at` would date a driver
    # to whenever a price breakdown last landed.
    #
    # The position is copied but `located_at` and `driver_location_at` are left
    # null on purpose — that pin was written by whichever webhook last carried
    # one and its age is unknown. `driver_proximity` reads a null as "we do not
    # know when this was true" and quotes no distance from it, which is the
    # right answer; the next push or sweep stamps a real one within the minute.
    op.execute(
        """
        INSERT INTO order_drivers (
            id, order_id, provider, courier_order_id, courier_driver_id,
            name, phone, plate, latitude, longitude,
            sequence, assigned_at, is_active, created_at, updated_at
        )
        SELECT gen_random_uuid(), d.order_id, d.provider, d.courier_order_id,
               d.driver_id, d.driver_name, d.driver_phone, d.driver_plate,
               d.driver_latitude, d.driver_longitude,
               1, d.booked_at, TRUE, now(), now()
          FROM order_deliveries d
         WHERE d.driver_id IS NOT NULL
            OR d.driver_name IS NOT NULL
        """
    )

    # And the same fact on the delivery's live copy. Left at zero these would
    # read as "nobody has ever been assigned", and the first poll after the
    # deploy would print a driver slip at every counter for every live order —
    # a stack of paper for drivers who have already collected.
    op.execute(
        """
        UPDATE order_deliveries
           SET driver_assignment_count = 1,
               driver_assigned_at = booked_at
         WHERE driver_id IS NOT NULL
            OR driver_name IS NOT NULL
        """
    )

    # The position sweep's own query: live bookings on an integrated courier,
    # stalest position first. Partial, so it indexes the handful of rows in
    # flight rather than every delivery the shop has ever made.
    op.execute(
        """
        CREATE INDEX ix_order_deliveries_live_driver
            ON order_deliveries (driver_location_at NULLS FIRST)
         WHERE courier_order_id IS NOT NULL
           AND driver_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_order_deliveries_live_driver")
    op.drop_column("order_deliveries", "driver_location_at")
    op.drop_column("order_deliveries", "driver_assigned_at")
    op.drop_column("order_deliveries", "driver_assignment_count")
    op.drop_index("ix_order_drivers_order_sequence", table_name="order_drivers")
    op.drop_index("ix_order_drivers_order_id", table_name="order_drivers")
    op.drop_table("order_drivers")
