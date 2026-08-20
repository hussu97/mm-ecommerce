"""Cache the driven route between the driver and the kitchen

The distance shown to the counter was a straight line times a fitted detour
factor. That factor is good enough to price a zone on average and poor at
answering "how long until they get here" for one rider: it does not know about
the creek, the one-ways round Al Majaz, or which bridge is between them and the
shop. A driver two kilometres away as the crow flies can be six of road.

Mapbox Directions answers both — road distance and a duration against traffic as
it is right now — and the answer is cached here rather than asked per render.
The register polls every twenty seconds, the admin every thirty, and several
terminals watch one branch; without a cache a single order would ask the same
question of a paid API a hundred times to get a number that changes once a
minute.

`driver_route_at` is what makes the cache honest. It is stamped when the route
was computed, not when the position under it was reported, so a route can be
stale for a reason the position is not — Mapbox unreachable, the token missing —
and `driver_proximity` can tell the difference and fall back rather than quote a
duration from a pin the driver has long left.

Revision ID: 113
Revises: 112
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

revision = "113_driver_route"
down_revision = "112_driver_reassign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "order_deliveries",
        sa.Column("driver_route_km", sa.Numeric(6, 1), nullable=True),
    )
    op.add_column(
        "order_deliveries",
        sa.Column("driver_route_minutes", sa.Numeric(5, 1), nullable=True),
    )
    op.add_column(
        "order_deliveries",
        sa.Column("driver_route_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Nothing is backfilled, deliberately. A route is a fact about a position at
    # a moment, and every position on the table predates this column — there is
    # no honest value to write. The sweep computes one within a minute for every
    # delivery that still has a driver coming, and `driver_proximity` quotes the
    # straight-line estimate meanwhile, which is exactly what it did yesterday.


def downgrade() -> None:
    op.drop_column("order_deliveries", "driver_route_at")
    op.drop_column("order_deliveries", "driver_route_minutes")
    op.drop_column("order_deliveries", "driver_route_km")
