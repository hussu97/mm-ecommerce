"""Batch groups, courier config, and the end of the two global delivery numbers.

Three changes that belong together, because each one is what makes the next
honest.

**Couriers become configurable.** "Only Lalamove batches" was a property that
returned `is_lalamove`; "noon Send means an hour" was one module constant applied
to every zone alike. Both are commercial facts that move without the code moving.

**Batching is declared rather than inferred.** `delivery_batch_windows` hung off
a single polygon, and the assignment code then merged any batches leaving on the
same minute — so which zones shared a courier booking fell out of two schedules
coincidentally ending together. Moving one zone's slot silently re-partitioned
another's. A window now belongs to a group, a group names its zones, and a run is
identified by `(group, dispatch_at)`.

**The two national numbers go.** `free_delivery_threshold` (150) and
`default_delivery_fee` (80) were a second source of truth for questions every
polygon already answers per-zone, and the admin printed both under "applies to
every zone" while no zone read either. The polygon's own threshold becomes NOT
NULL, and a pin outside every polygon becomes unserviceable rather than charged
80 — the active map tiles the whole country, so "outside every zone" now means
outside the country.

The data mapping is the one agreed: the three Dubai bands ride together at 90
minutes, the four northern city zones at 120. Sharjah Central (noon Send) and
every third-party zone dispatch immediately and join no group.

Revision ID: 088_batch_groups_and_couriers
Revises: 087_phone_verification
Create Date: 2026-08-08
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "088_batch_groups_and_couriers"
down_revision: Union[str, None] = "087_phone_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: (code, name, supports_batching, kind, minutes)
COURIERS = [
    ("lalamove", "Lalamove", True, "minutes", 60),
    ("noon_send", "noon Send", False, "minutes", 60),
    ("third_party", "Third party", False, "next_day", None),
]

#: (group name, courier, minutes after dispatch, zones, the zone whose windows
#: the group inherits)
GROUPS = [
    (
        "Dubai",
        "lalamove",
        90,
        ("Dubai Near", "Dubai Mid", "Dubai Far"),
        "Dubai Near",
    ),
    (
        "Northern Emirates",
        "lalamove",
        120,
        ("Sharjah Outer", "Ajman City", "Umm al-Quwain City", "Ras al-Khaimah City"),
        "Sharjah Outer",
    ),
]


def upgrade() -> None:
    # ── couriers ──────────────────────────────────────────────────────────
    op.create_table(
        "couriers",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("code", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column(
            "supports_batching", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "unbatched_promise_kind",
            sa.String(20),
            nullable=False,
            server_default="next_day",
        ),
        sa.Column("unbatched_promise_minutes", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
    op.create_index("ix_couriers_code", "couriers", ["code"], unique=True)

    for code, name, batching, kind, minutes in COURIERS:
        op.execute(
            sa.text(
                "INSERT INTO couriers (code, name, supports_batching, "
                "unbatched_promise_kind, unbatched_promise_minutes) "
                "VALUES (:code, :name, :batching, :kind, :minutes)"
            ).bindparams(
                code=code, name=name, batching=batching, kind=kind, minutes=minutes
            )
        )

    # ── batch groups ──────────────────────────────────────────────────────
    op.create_table(
        "delivery_batch_groups",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column(
            "courier_code",
            sa.String(20),
            sa.ForeignKey("couriers.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "delivery_minutes_after_dispatch",
            sa.Integer(),
            nullable=False,
            server_default="90",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
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
        "ix_delivery_batch_groups_courier_code",
        "delivery_batch_groups",
        ["courier_code"],
    )

    op.add_column(
        "delivery_polygons",
        sa.Column(
            "batch_group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_batch_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_delivery_polygons_batch_group_id", "delivery_polygons", ["batch_group_id"]
    )

    # Windows move from a polygon to a group. Added nullable, backfilled, then
    # made required — an ALTER that cannot be done in one step on a table with
    # rows in it.
    op.add_column(
        "delivery_batch_windows",
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_batch_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "delivery_batches",
        sa.Column(
            "group_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_batch_groups.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )

    for name, courier, minutes, zones, source_zone in GROUPS:
        op.execute(
            sa.text(
                "INSERT INTO delivery_batch_groups "
                "(name, courier_code, delivery_minutes_after_dispatch) "
                "VALUES (:name, :courier, :minutes)"
            ).bindparams(name=name, courier=courier, minutes=minutes)
        )
        # Only zones on the *active* map. An old version's shapes keep their
        # history and are not dragged onto a live schedule.
        op.execute(
            sa.text(
                "UPDATE delivery_polygons p "
                "SET batch_group_id = g.id "
                "FROM delivery_batch_groups g, delivery_polygon_versions v "
                "WHERE g.name = :name AND v.id = p.version_id AND v.is_active "
                "AND p.name = ANY(:zones)"
            ).bindparams(name=name, zones=list(zones))
        )
        # The group inherits one zone's schedule rather than the union of all of
        # them: the zones in a group were given identical windows precisely so
        # they would collide into one run, so the union would be duplicates.
        op.execute(
            sa.text(
                "UPDATE delivery_batch_windows w "
                "SET group_id = g.id "
                "FROM delivery_batch_groups g, delivery_polygons p, "
                "     delivery_polygon_versions v "
                "WHERE g.name = :name AND w.polygon_id = p.id "
                "AND v.id = p.version_id AND v.is_active AND p.name = :source"
            ).bindparams(name=name, source=source_zone)
        )

    # A pending run follows its window to the new group; anything whose window
    # is gone, or that belonged to a zone now dispatching immediately, is
    # cancelled rather than left pointing nowhere. Those orders are still packed
    # and still need a driver — `status = cancelled` is what puts them in front
    # of a human, which is the same place a failed booking lands.
    op.execute(
        "UPDATE delivery_batches b SET group_id = w.group_id "
        "FROM delivery_batch_windows w "
        "WHERE b.window_id = w.id AND w.group_id IS NOT NULL"
    )
    op.execute(
        "UPDATE delivery_batches SET status = 'cancelled' "
        "WHERE group_id IS NULL AND status = 'pending'"
    )
    op.execute("DELETE FROM delivery_batches WHERE group_id IS NULL")
    # Windows that belonged to a zone now dispatching immediately.
    op.execute("DELETE FROM delivery_batch_windows WHERE group_id IS NULL")

    op.alter_column("delivery_batch_windows", "group_id", nullable=False)
    op.alter_column("delivery_batches", "group_id", nullable=False)
    op.create_index(
        "ix_delivery_batch_windows_group_id", "delivery_batch_windows", ["group_id"]
    )
    op.create_index("ix_delivery_batches_group_id", "delivery_batches", ["group_id"])
    op.drop_column("delivery_batch_windows", "polygon_id")
    op.drop_column("delivery_batches", "polygon_id")

    # ── the two globals ───────────────────────────────────────────────────
    # Backfill before the NOT NULL, so a zone that was relying on the national
    # number keeps the number it was actually being priced against.
    op.execute(
        "UPDATE delivery_polygons p SET free_delivery_threshold = "
        "COALESCE(p.free_delivery_threshold, "
        "(SELECT s.free_delivery_threshold FROM delivery_settings s LIMIT 1), 150.00)"
    )
    op.alter_column(
        "delivery_polygons",
        "free_delivery_threshold",
        nullable=False,
        server_default="150.00",
    )
    op.drop_column("delivery_settings", "free_delivery_threshold")
    op.drop_column("delivery_settings", "default_delivery_fee")


def downgrade() -> None:
    op.add_column(
        "delivery_settings",
        sa.Column(
            "default_delivery_fee",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="50.00",
        ),
    )
    op.add_column(
        "delivery_settings",
        sa.Column(
            "free_delivery_threshold",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="150.00",
        ),
    )
    op.alter_column(
        "delivery_polygons",
        "free_delivery_threshold",
        nullable=True,
        server_default=None,
    )

    # The schedule cannot be put back on the zone it came from — a group's
    # windows described several — so the down path drops the runs and the slots
    # and leaves the zones dispatching immediately. Recoverable by republishing a
    # schedule; not recoverable by guessing.
    op.add_column(
        "delivery_batches",
        sa.Column(
            "polygon_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_polygons.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "delivery_batch_windows",
        sa.Column(
            "polygon_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_polygons.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.execute("DELETE FROM delivery_batches")
    op.execute("DELETE FROM delivery_batch_windows")
    op.alter_column("delivery_batches", "polygon_id", nullable=False)
    op.alter_column("delivery_batch_windows", "polygon_id", nullable=False)

    op.drop_index("ix_delivery_batches_group_id", table_name="delivery_batches")
    op.drop_index(
        "ix_delivery_batch_windows_group_id", table_name="delivery_batch_windows"
    )
    op.drop_column("delivery_batches", "group_id")
    op.drop_column("delivery_batch_windows", "group_id")

    op.drop_index("ix_delivery_polygons_batch_group_id", table_name="delivery_polygons")
    op.drop_column("delivery_polygons", "batch_group_id")
    op.drop_index(
        "ix_delivery_batch_groups_courier_code", table_name="delivery_batch_groups"
    )
    op.drop_table("delivery_batch_groups")
    op.drop_index("ix_couriers_code", table_name="couriers")
    op.drop_table("couriers")
