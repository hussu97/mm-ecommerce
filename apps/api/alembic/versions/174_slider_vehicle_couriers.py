"""Register Slider's two vehicle tiers as couriers, and seed their alternates.

Slider prices a bike and a car differently, and a zone now names the tier it was
drawn for — `slider_bike` or `slider_car` — so a quote and its booking agree on
a vehicle without recomputing it. Both are couriers in their own right here, for
the same reason the bare `slider` row exists (migration `127`): `delivery_promise`
reads `couriers` to answer "when will this arrive", and a provider with no row
falls through to the `next_day` default — an hour's delivery promised for
tomorrow, everywhere, with nothing reporting a problem.

**Sixty minutes now, not the ninety `127` gave the bare `slider` row.** That row
said ninety only because the pilot gate handed all but one account back to noon
Send (ninety inside Sharjah), so a sixty-minute promise would have been read off
a courier that was not carrying it. The gate is gone; Slider carries its own
zones, and sixty is Slider's own figure.

`supports_batching` is false for both — Slider has no multi-stop endpoint — and
`batching_service.reserve` already refuses to batch anything that is not
literally Lalamove, so this changes no dispatch behaviour today.

This is also the migration that **introduces** these two providers to the
alternates matrix (`test_zone_alternate_defaults` requires every provider be
seeded by a named migration). No polygon names them yet — the per-area map that
does arrives in a later migration and sets each polygon's `alternate_providers`
explicitly on the way in — so the guarded UPDATE below matches nothing today and
seeds the default the moment such a zone exists without one.

Revision ID: 174_slider_vehicle_couriers
Revises: 173_branch_weekly_single_shift
Create Date: 2026-09-04
"""

from __future__ import annotations

import json
from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "174_slider_vehicle_couriers"
down_revision: Union[str, None] = "173_branch_weekly_single_shift"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (code, display name, minutes to the door). A car is the same promise as a
#: bike — Slider quotes both at sixty — but a separate row so the console can
#: reprice one without the other.
COURIERS = (
    ("slider_bike", "Slider (bike)", 60),
    ("slider_car", "Slider (car)", 60),
)

#: The default a zone naming one of these gets when nobody has said otherwise —
#: the copy of `DEFAULT_ALTERNATES` this migration is responsible for. A bike may
#: be upgraded to a car by hand (one-way); a car may not become a bike, so Slider
#: is not among its alternates at all.
ALTERNATES = {
    "slider_bike": ["slider_car", "lalamove", "third_party"],
    "slider_car": ["lalamove", "third_party"],
}


def upgrade() -> None:
    for code, name, minutes in COURIERS:
        op.execute(
            sa.text(
                "INSERT INTO couriers (code, name, supports_batching, "
                "unbatched_promise_kind, unbatched_promise_minutes) "
                "VALUES (:code, :name, false, 'minutes', :minutes) "
                # Idempotent: a database restored from a dump taken after somebody
                # added the row by hand should not fail to migrate.
                "ON CONFLICT (code) DO NOTHING"
            ).bindparams(code=code, name=name, minutes=minutes)
        )

    # Seed the alternates only where a zone names the provider and has none yet.
    # Guarded so it cannot fight an admin edit, and matches nothing until the
    # per-area map introduces such a zone.
    for provider, alternates in ALTERNATES.items():
        op.execute(
            sa.text(
                "UPDATE delivery_polygons "
                "SET alternate_providers = CAST(:alts AS jsonb) "
                "WHERE fulfilment_provider = :provider "
                "AND (alternate_providers IS NULL "
                "OR alternate_providers = '[]'::jsonb)"
            ).bindparams(alts=json.dumps(alternates), provider=provider)
        )


def downgrade() -> None:
    for code, _name, _minutes in COURIERS:
        # Restricted by `delivery_batch_groups.courier_code`; a zone still naming
        # one would be a next-day promise on an hour-long delivery.
        op.execute(
            sa.text("DELETE FROM couriers WHERE code = :code").bindparams(code=code)
        )
