"""The basket remembers which promo code is on it

The applied code travelled from the basket to the checkout through
`sessionStorage`, and nothing else carried it. When that write failed — private
mode, a full quota — the checkout learned nothing, priced the order without the
discount and **submitted it that way**: both `useOrderPreview` and the order
itself gate on `promoDiscount > 0`, so a lost handoff is a customer who is shown
one price in the basket and charged another.

That is exactly what happened on 20 August. The basket read 29.75 with
`Discount (NEW) -5.25` and the checkout read 35.00 with an empty promo field —
not a refused code, an absent one.

**The code, and never the discount.** What a coupon is worth depends on the
basket, the identity and the day, and it is computed server-side every time it
is asked (CLAUDE.md rule 10). Storing the amount here would be a second copy of
a number the server already owns, free to drift from it. The checkout re-asks
`/promo-codes/validate` with the code and gets today's answer.

Nullable and unconstrained by design: it holds whatever the customer typed and
we accepted, and a code deleted from the console afterwards simply fails
validation at the checkout like any other.

Revision ID: 115
Revises: 114
Create Date: 2026-08-20
"""

import sqlalchemy as sa

from alembic import op

revision = "115_cart_promo_code"
down_revision = "114_one_cart_per_session"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("carts", sa.Column("promo_code", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("carts", "promo_code")
