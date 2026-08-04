"""Give a branch the details a customer needs to come and collect from it.

Until now "pickup" was one hardcoded pin in the checkout page and a branch the
API guessed at afterwards, so the two could disagree and neither the
confirmation email nor the account page could say where to go. A customer who
chooses collection is choosing a *place*, and a place needs a name they can
read, an address they can follow, a city so they can tell two shops apart, and
a map link.

Four columns:

* ``city`` / ``city_localized`` — the city as a customer would name it. Not
  parsed out of ``address``: that column is one free-text line written for a
  driver, and its last comma-separated fragment is a guess rather than a city.
* ``address_localized`` — the Arabic address, next to the Arabic name that is
  already there.
* ``offers_pickup`` — whether a customer may choose to collect from here. It is
  deliberately not inferred from ``receives_online_orders``: that flag says the
  branch *bakes* website orders, which a production kitchen can do without being
  somewhere we would send a customer. Default false, so a kitchen added later
  cannot appear on the checkout by accident.

Both live branches are backfilled and flagged. K001 is the Sharjah kitchen the
checkout has been pointing at all along — its pin here is the same verified one
055 wrote — and B001 is the Barsha Heights counter from 056.

Revision ID: 070_branch_pickup_details
Revises: 069_branch_outlet_address_code
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "070_branch_pickup_details"
down_revision: Union[str, None] = "069_branch_outlet_address_code"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: reference -> what we know about the place. Applied with COALESCE on the
#: free-text columns so a value an admin has since corrected by hand is never
#: overwritten; the city columns and the flag are new and simply set.
BRANCHES: dict[str, dict[str, object]] = {
    "K001": {
        "name_localized": "ملتينج مومنتس كيكس",
        "address": (
            "Melting Moments Cakes, Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah"
        ),
        "address_localized": (
            "ملتينج مومنتس كيكس، جاردن تاور 1 محل رقم 1، المجاز 3، الشارقة"
        ),
        "city": "Sharjah",
        "city_localized": "الشارقة",
        "latitude": 25.3304139,
        "longitude": 55.3736131,
    },
    "B001": {
        "name_localized": "ملتينج مومنتس - برشا هايتس",
        "address": ("Attibassi Coffee, Al Shafar Tower 1, Barsha Heights, Dubai"),
        "address_localized": "قهوة عتيبة، برج الشعفار 1، برشا هايتس، دبي",
        "city": "Dubai",
        "city_localized": "دبي",
        "latitude": 25.0984482,
        "longitude": 55.1741736,
    },
}


def upgrade() -> None:
    op.add_column("branches", sa.Column("city", sa.String(length=100), nullable=True))
    op.add_column(
        "branches", sa.Column("city_localized", sa.String(length=100), nullable=True)
    )
    op.add_column("branches", sa.Column("address_localized", sa.Text(), nullable=True))
    op.add_column(
        "branches",
        sa.Column(
            "offers_pickup",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index("ix_branches_offers_pickup", "branches", ["offers_pickup"])

    conn = op.get_bind()
    for reference, details in BRANCHES.items():
        conn.execute(
            sa.text(
                "UPDATE branches SET "
                "  name_localized = COALESCE(name_localized, :name_localized), "
                "  address = COALESCE(address, :address), "
                "  address_localized = :address_localized, "
                "  city = :city, "
                "  city_localized = :city_localized, "
                "  latitude = COALESCE(latitude, :latitude), "
                "  longitude = COALESCE(longitude, :longitude), "
                "  offers_pickup = true "
                "WHERE reference = :reference AND deleted_at IS NULL"
            ),
            {"reference": reference, **details},
        )


def downgrade() -> None:
    op.drop_index("ix_branches_offers_pickup", table_name="branches")
    op.drop_column("branches", "offers_pickup")
    op.drop_column("branches", "address_localized")
    op.drop_column("branches", "city_localized")
    op.drop_column("branches", "city")
