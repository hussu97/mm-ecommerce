"""Point the Slider zones at Slider.

Six zones change hands on the map and **nobody's delivery changes hands with
them**, because `courier_service.carrier_for` gates Slider to the accounts in
`SLIDER_TRIAL_EMAILS` and hands every other customer straight back to whoever
carried that ground before: noon Send inside Sharjah, Lalamove outside it. With
that list empty — which is how this ships — the map says Slider and every single
order still goes exactly where it went yesterday.

That is the whole reason this is safe to run ahead of the courier being proven,
and it is why it is a migration of its own rather than part of `125`: the
geometry and the routing are two decisions, each independently reversible.

Which six, and why:

    Sharjah Core          from noon Send. The 3.5 km ring where Slider's car
                          beats their bike — by about AED 1 an order, which is
                          not the reason. Dispatch accuracy is.
    Ajman City            from Lalamove. The genuine win: Lalamove's 17 AED
                          base is high and Ajman is close, so Slider's car is
                          cheaper on all eleven measured areas, by 1.98 mean.
    Dubai Near/Mid/Far    from Lalamove, at +2.29 an order. Accepted.
    Umm al-Quwain City    from Lalamove, at +1.93 an order. Accepted.

Sharjah Outer and the Ras al-Khaimah bands stay on Lalamove (+4.18 and worse),
and everything at Abu Dhabi / Al Ain / Fujairah / East Coast distance stays
where it is at +8.4 to +12.1. Across the whole Slider car field — Ajman, Dubai
and Umm al-Quwain, 36 areas — the change costs about AED 0.94 an order. That is
the price of the reliability and it is small.

**Guarded so it cannot fight the admin.** Each update names the provider it
means to replace as well as the zone, and touches only the active map. Once
somebody moves one of these zones in the console the migration matches nothing
and does nothing — including on a database restored from an older dump.

**The batch groups stay exactly where they are**, which looks wrong for a courier
that cannot be batched and is the only thing that keeps the promise honest. A
grouped zone is promised its group's next window close plus the group's minutes;
an ungrouped one is promised its courier's own answer. Detaching `Ajman City`
and the three Dubai bands would therefore change what every customer in those
zones is told the moment this runs — from "after the next run leaves" to "within
the hour" — while their orders carried on riding the Lalamove run, because
`batching_service.reserve` asks `courier_service.carrier_for` who is *actually*
carrying it and gets Lalamove for everyone off the list.

So the group is not a schedule Slider rides. It is the schedule the zone's
**fallback** rides, and it must survive for as long as the fallback carries
almost every order in the zone. `couriers.supports_batching` is false for Slider,
which is what stops an admin attaching a *new* schedule to one.

Revision ID: 128_slider_zones
Revises: 127_slider_courier
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "128_slider_zones"
down_revision: Union[str, None] = "127_slider_courier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SLIDER = "slider"

#: (zone, the provider it must currently name, where its orders may be moved
#: once Slider carries it, where they may be moved if this is rolled back).
#:
#: Naming the old provider as well as the zone is the guard: once somebody moves
#: one of these in the console this matches nothing and does nothing, including
#: on a restored dump.
#:
#: The alternates travel with the courier because they are a statement *about*
#: the courier — "where may an order go when this one will not carry it" — and
#: leaving `Ajman City` offering `["third_party"]`, which is what a Lalamove
#: zone offers, would mean a stuck Slider order could not be handed to Lalamove
#: at all. Which is the very thing the automatic gate does for it on every
#: order but the pilot account's.
#:
#: `Sharjah Core` keeps noon Send among its alternates and gains Lalamove,
#: because inside Sharjah both are real answers and the gate itself falls back
#: to noon Send there. The other five do not: noon Send cannot cross an emirate
#: boundary, so offering them it would be offering a refusal.
ZONES: list[tuple[str, str, str, str]] = [
    (
        "Sharjah Core",
        "noon_send",
        '["noon_send", "lalamove", "third_party"]',
        '["third_party", "lalamove"]',
    ),
    ("Ajman City", "lalamove", '["lalamove", "third_party"]', '["third_party"]'),
    ("Dubai Near", "lalamove", '["lalamove", "third_party"]', '["third_party"]'),
    ("Dubai Mid", "lalamove", '["lalamove", "third_party"]', '["third_party"]'),
    ("Dubai Far", "lalamove", '["lalamove", "third_party"]', '["third_party"]'),
    (
        "Umm al-Quwain City",
        "lalamove",
        '["lalamove", "third_party"]',
        '["third_party"]',
    ),
]


def upgrade() -> None:
    conn = op.get_bind()
    for name, was, alternates, _back in ZONES:
        conn.execute(
            sa.text(
                "UPDATE delivery_polygons p SET fulfilment_provider = :now, "
                "    alternate_providers = CAST(:alternates AS jsonb) "
                "FROM delivery_polygon_versions v "
                "WHERE v.id = p.version_id AND v.is_active "
                "AND p.name = :name AND p.fulfilment_provider = :was"
            ),
            {"now": SLIDER, "name": name, "was": was, "alternates": alternates},
        )


def downgrade() -> None:
    conn = op.get_bind()
    for name, was, _alternates, back in ZONES:
        conn.execute(
            sa.text(
                "UPDATE delivery_polygons p SET fulfilment_provider = :was, "
                "    alternate_providers = CAST(:back AS jsonb) "
                "FROM delivery_polygon_versions v "
                "WHERE v.id = p.version_id AND v.is_active "
                "AND p.name = :name AND p.fulfilment_provider = :now"
            ),
            {"was": was, "now": SLIDER, "name": name, "back": back},
        )
