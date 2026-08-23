"""A zone says which couriers its orders may be moved to, not just which carries them.

`delivery_polygons.fulfilment_provider` has always been the zone's courier, and
until now it was the *only* thing said about dispatch. So when a courier went
quiet there was exactly one escape in the whole system — a packed third-party
order onto Lalamove, in that direction and no other — and every other stuck
order needed somebody to ring somebody.

This adds the other half of the sentence. `fulfilment_provider` becomes the
**preferred** courier and is otherwise untouched; `alternate_providers` is where
an order may go when the preferred one will not carry it.

**Not "every other courier".** noon Send cannot cross an emirate boundary and
will not take a run past 20 km, so a Dubai order handed to them is a refusal at
best. The seeded defaults therefore give a Lalamove zone third party and *not*
noon Send — which is precisely what `courier_service._dispatch_once` already
does automatically, falling noon Send back to Lalamove and never the reverse.
The policy is not new; being able to read it out of a row, and edit it without a
deploy, is.

**Guarded, per convention 7.** Each backfill matches `alternate_providers =
'[]'` — a zone nobody has configured. The moment somebody sets alternates in the
console this migration matches nothing and does nothing, including when replayed
against a database restored from an older dump. That is what stops a deploy
quietly overwriting an operator's decision.

It sweeps **every** version's polygons rather than only the active map's. A
version is a rollback target; publishing last month's map and finding its zones
had no alternates would be the same outage this exists to prevent.

**The one thing the guard cannot see** is a zone an operator has deliberately
set to *no* alternates. That is written `[]`, which is also what "nobody has
been here yet" is written as, so a replay re-seeds it. Said out loud rather than
worked around: the alternative is a nullable column whose three states nothing
else in this table has, to protect a setting whose whole effect is to refuse a
move that a person is standing there asking for. If a zone genuinely must never
be moved off its courier, that wants a flag of its own and an explanation.

`DEFAULT_ALTERNATES` in `app/models/delivery_polygon.py` holds the same matrix
for the running code. The copy here is deliberate — a migration has to keep
describing the database as it was, so it must not import a constant that will be
edited later. `tests/unit/test_zone_alternate_defaults.py` compares the two so
they cannot drift.

Revision ID: 125_zone_alternates
Revises: 124_device_build_platform
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "125_zone_alternates"
down_revision: Union[str, None] = "124_device_build_platform"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: preferred courier -> what its orders may be moved to.
#:
#: Mirrored by `DEFAULT_ALTERNATES` in `app/models/delivery_polygon.py`. Spelled
#: out here rather than imported, on purpose: see the module docstring.
DEFAULTS: tuple[tuple[str, str], ...] = (
    ("lalamove", '["third_party"]'),
    ("third_party", '["lalamove"]'),
    ("noon_send", '["third_party", "lalamove"]'),
)


def upgrade() -> None:
    bind = op.get_bind()

    # Added only if it is not already there.
    #
    # Not defensive decoration: an operator recovering from a bad deploy stamps
    # back a revision and runs forward again, and a bare `add_column` answers
    # that with `DuplicateColumnError` — loudly, but in the middle of a deploy,
    # which is the worst moment to be reading a stack trace. The seed below is
    # separately guarded, so running the whole thing twice is a no-op rather
    # than a failure.
    columns = {c["name"] for c in sa.inspect(bind).get_columns("delivery_polygons")}
    if "alternate_providers" not in columns:
        op.add_column(
            "delivery_polygons",
            sa.Column(
                "alternate_providers",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default="[]",
            ),
        )

    for preferred, alternates in DEFAULTS:
        op.execute(
            sa.text(
                """
                UPDATE delivery_polygons
                   SET alternate_providers = CAST(:alternates AS jsonb)
                 WHERE fulfilment_provider = :preferred
                   AND alternate_providers = '[]'::jsonb
                """
            ).bindparams(alternates=alternates, preferred=preferred)
        )


def downgrade() -> None:
    op.drop_column("delivery_polygons", "alternate_providers")
