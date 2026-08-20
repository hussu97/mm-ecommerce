"""A basket says who is holding it and when they last touched it

Two columns, both in service of one gap `tasks/conversion-audit.md` names as the
largest recoverable revenue line on the site: nothing anywhere knows that a
basket has been sitting untouched for six hours, and for a guest there is no
address to say anything to about it.

**`guest_email`** — the address typed into the checkout form, written back to
the basket it belongs to. It was already collected (`OrderPreviewRequest.email`,
where it judges a new-customer coupon) and then dropped on the floor: a shopper
who filled the form in and closed the tab left nothing behind but a row nobody
could contact. Only ever written for a basket with no `user_id`, because a
signed-in basket already has an address on `users.email` and a second copy of it
here would be free to go stale the day somebody changes their account email.

**`last_activity_at`** — when this basket was last touched by the person
holding it. `updated_at` cannot answer that: adding a line writes `cart_items`
and never touches the `carts` row at all, so a basket that has been actively
filled for ten minutes can carry an `updated_at` from when it was created. This
column is stamped by `cart_service.touch` on every read and every write of the
basket, throttled to a minute so an ordinary browsing session is not one row
update per page view.

Backfilled from `updated_at` rather than left null. It is the wrong answer by
exactly the amount described above, but it is the right *order*, and a console
sorting by "most recently active" is more useful with an approximate history
than with every pre-existing basket sharing one empty bucket at the bottom.

Both are nullable and unconstrained. Neither is a lifecycle status, so neither
takes a CHECK (CLAUDE.md rule 6), and neither is content the deploy has to carry
— there is no admin edit for either to fight (rule 7).

Revision ID: 116
Revises: 115
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

revision = "116_basket_activity"
down_revision = "115_cart_promo_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("carts", sa.Column("guest_email", sa.String(255), nullable=True))
    op.add_column(
        "carts",
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Structural: the column's whole purpose is to be ordered and filtered by,
    # and every row that exists on the morning of the deploy would otherwise
    # sort as "never active". See the module docstring for why this is an
    # approximation rather than the truth.
    op.execute("UPDATE carts SET last_activity_at = updated_at")

    # The console lists baskets newest-activity-first and the recovery sweep, if
    # it is ever built, asks the same question with a cutoff. Both are this
    # index.
    op.create_index("ix_carts_last_activity_at", "carts", ["last_activity_at"])
    # Reaching a named shopper is the only thing this column is for, so the one
    # query against it is "the ones we can write to".
    op.create_index("ix_carts_guest_email", "carts", ["guest_email"])


def downgrade() -> None:
    op.drop_index("ix_carts_guest_email", table_name="carts")
    op.drop_index("ix_carts_last_activity_at", table_name="carts")
    op.drop_column("carts", "last_activity_at")
    op.drop_column("carts", "guest_email")
