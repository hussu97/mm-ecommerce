"""Remember which language an order was placed in.

Every email the shop sends about an order was English, because nothing on the
order said otherwise. A customer who browsed the site in Arabic, read an Arabic
checkout and paid in Arabic then got "Your order is confirmed" — and, later,
"We couldn't hand it over" — in a language they may not read at all.

The language is a property of the order rather than of the customer. A guest has
no account to hang a preference on, a shared account can be used by two people
who read different languages, and the useful question is not "what does this
person prefer" but "what were they reading when they placed this". That is known
exactly once, at checkout, and it does not change afterwards — so it is captured
with the row.

Two characters, because that is what a locale code is here (`en`, `ar`) and the
`languages` table keys on the same short codes.

NOT NULL with a server default of `en`: every order that already exists was
placed in English as far as anyone can tell, and an order with no language is
not a quieter order — it is one the mailer has to guess about.

Revision ID: 071_order_locale
Revises: 070_branch_pickup_details
Create Date: 2026-08-04
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "071_order_locale"
down_revision: Union[str, None] = "070_branch_pickup_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column(
            "locale",
            sa.String(length=5),
            nullable=False,
            server_default="en",
        ),
    )


def downgrade() -> None:
    op.drop_column("orders", "locale")
