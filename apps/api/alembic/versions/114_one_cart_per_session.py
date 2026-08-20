"""One cart per session, enforced by the database

`carts` had a unique index on `user_id` and nothing at all on `session_id`, and
every lookup for a guest's basket is `WHERE session_id = ?` followed by
`scalar_one_or_none()`. That call asserts a uniqueness this table did not have.

Two concurrent add-to-cart requests for one guest — a double tap on a phone, a
retry after a slow response — both find no cart and both create one. From then
on every read of that basket raises `MultipleResultsFound`, so **adding to the
cart and loading the cart both answer 500** until the rows are cleared. Six
customers hit it in the week before this migration; one of them is on a session
recording, dead-clicking an upsell and then watching "Loading your cart…" spin
for the rest of the session.

The index is on `session_id` across **all** carts rather than only guest ones,
because the lookup does not filter on `user_id` either — a cart claimed by a
login keeps its `session_id`, and the guest branch would still match it.

Revision ID: 114
Revises: 113
Create Date: 2026-08-20
"""

from alembic import op

revision = "114_one_cart_per_session"
down_revision = "113_driver_route"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── anything already doubled up ───────────────────────────────────────────
    #
    # Production has none as this is written — the rows are cleared when a cart
    # is consumed, which is why the evidence keeps disappearing behind the
    # symptom. This runs for the database that is restored from an older dump,
    # where a live duplicate would otherwise make the index below fail to build
    # and take the deploy down with it.
    #
    # Items move to the oldest cart rather than the newest being dropped: the
    # customer put every one of them in deliberately, `cart_items` has no
    # uniqueness to violate, and a duplicated line is something they can remove
    # while a vanished one is something they have to notice.
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   session_id,
                   row_number() OVER (
                       PARTITION BY session_id ORDER BY created_at, id
                   ) AS rn,
                   first_value(id) OVER (
                       PARTITION BY session_id ORDER BY created_at, id
                   ) AS keep_id
              FROM carts
             WHERE session_id IS NOT NULL
        )
        UPDATE cart_items
           SET cart_id = ranked.keep_id
          FROM ranked
         WHERE cart_items.cart_id = ranked.id
           AND ranked.rn > 1
        """
    )
    op.execute(
        """
        DELETE FROM carts
         WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY session_id ORDER BY created_at, id
                       ) AS rn
                  FROM carts
                 WHERE session_id IS NOT NULL
            ) d WHERE d.rn > 1
         )
        """
    )

    # ── and it cannot happen again ────────────────────────────────────────────
    #
    # This is what makes the race impossible rather than unlikely: the losing
    # INSERT now raises `IntegrityError`, which `cart_service` catches and
    # answers by re-reading the cart the winner made. Without it, the retry in
    # the service would be guessing that nobody else got there first.
    op.execute(
        """
        CREATE UNIQUE INDEX uq_carts_session_id
            ON carts (session_id)
         WHERE session_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_carts_session_id")
