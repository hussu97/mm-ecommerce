"""Register Slider as a courier.

One row, and its absence would be silent rather than loud. `delivery_promise`
reads `couriers` to answer "when will this arrive"; a provider with no row falls
through to the default, which is `next_day` — so an order Slider will deliver in
an hour would be promised for tomorrow, on the checkout, in the confirmation
email and on the register, with nothing anywhere reporting a problem.

`supports_batching` is false and that is a statement about their product rather
than about our appetite: Slider has no multi-stop endpoint, so a run cannot be
shared. `batching_service.reserve` already refuses to batch anything that is not
literally Lalamove, so this row changes no behaviour today — it stops an admin
attaching a schedule to a Slider zone tomorrow.

**Ninety minutes to the door, not sixty**, and the difference is deliberate.
Sixty is Slider's own figure and is what this row should say once the pilot
opens to everybody. Today it must not, because the promise is read off the
*zone's* declared courier while the pilot gate hands all but one account back to
somebody else — and inside Sharjah that somebody is noon Send, whose promise the
shop settled at ninety in `108`. A row saying sixty would tell every customer in
`Sharjah Core` their cake arrives within the hour and then hand it to a courier
promising an hour and a half.

Ninety is also simply the safe direction to be wrong in, and it is one edit in
Admin → Delivery → Couriers when the gate comes off, not another migration.

Everywhere else this row is not consulted at all: the Ajman, Dubai and Umm
al-Quwain Slider zones stay in their batch groups, and a grouped zone is
promised its group's next window close.

Revision ID: 126_slider_courier
Revises: 125_cost_banded_map_v2
Create Date: 2026-08-21
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "126_slider_courier"
down_revision: Union[str, None] = "125_cost_banded_map_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CODE = "slider"


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO couriers (code, name, supports_batching, "
            "unbatched_promise_kind, unbatched_promise_minutes) "
            "VALUES (:code, 'Slider', false, 'minutes', 90) "
            # Idempotent, because a database restored from a dump taken after
            # somebody added the row by hand should not fail to migrate.
            "ON CONFLICT (code) DO NOTHING"
        ).bindparams(code=CODE)
    )


def downgrade() -> None:
    # Only if no zone still names it — `delivery_batch_groups.courier_code`
    # restricts the delete, and a zone pointing at a courier row that no longer
    # exists is a next-day promise on an hour-long delivery.
    op.execute(sa.text("DELETE FROM couriers WHERE code = :code").bindparams(code=CODE))
