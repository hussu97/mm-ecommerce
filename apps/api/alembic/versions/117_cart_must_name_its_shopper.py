"""A cart must name whose it is

`carts` allowed a row with no `user_id` and no `session_id`, and
`identity_clause` looked a guest's basket up as `WHERE session_id = ?`. For a
request that carried no session id at all that parameter was `None`, which
SQLAlchemy renders as `session_id IS NULL` — so an identity-less row is not an
orphan, it is a basket that **every** session-less shopper on the internet
matches at once.

Production grew exactly one, `d5616cac-7719-4d9a-b785-30586feb1ad8`, on
8 March 2026. Four different customers' items accumulated in it over the five
months that followed — brownies in March, a caramel cake slice in August, a
cookie melt six hours later, a lemon raspberry slice six hours after that. It
was reachable because `add_item` was the one cart operation that did not first
ask who was shopping: every other path raised `BadRequestError`, so the shape
of the bug from the customer's side was an add that answered 201 followed by a
quantity stepper that answered 400. Three shoppers hit that in the thirteen
hours of container logs that were still on the VM when it was found, and all
three 400s name the same `cart_items.id` — the caramel slice a stranger had put
in the shared basket, rendered into their cart from the add-to-cart response.

Three things had to be true for that row to matter, so all three are closed:

* it could be **found** — `identity_clause` now answers `false()` rather than
  `IS NULL` for a request with neither identity;
* it could be **made** — `require_identity` now guards `get_or_create_cart`,
  which is the one function every write path passes through;
* it could be **stored** — which is this migration.

`carts_user_id_fkey` moves from `ON DELETE SET NULL` to `ON DELETE CASCADE` in
the same breath, and that is not scope creep: with the check constraint in
place, `SET NULL` on a signed-in shopper's cart (which carries no `session_id`,
having been created by `get_or_create_cart(db, user_id, None)`) would produce
exactly the row the constraint forbids, and the user deletion would fail on it.
A basket whose owner no longer exists is not a basket; cascading is both what
keeps the constraint satisfiable and what the row meant all along. No code path
deletes a user today, which is why this has never been exercised — the point is
that the first one to do so does not meet a constraint violation.

Revision ID: 117
Revises: 116
Create Date: 2026-08-21
"""

from alembic import op

revision = "117_cart_must_name_its_shopper"
down_revision = "116_basket_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── the rows that already have nobody ─────────────────────────────────────
    #
    # Deleted rather than re-homed, which is the opposite of what migration 114
    # did with its duplicates, and for the opposite reason: 114 knew whose the
    # items were and merely had two rows to choose between, whereas these lines
    # were added by four different people and there is no identity left on the
    # row to attribute any of them to. A basket nobody can open is not something
    # a customer can notice the loss of — and after this migration nothing can
    # reach it, so leaving it would only be leaving one customer's cake slice
    # sitting in the database forever.
    #
    # `cart_items.cart_id` is `ON DELETE CASCADE`, so the lines go with it.
    op.execute(
        """
        DELETE FROM carts
         WHERE user_id IS NULL
           AND (session_id IS NULL OR session_id = '')
        """
    )

    # ── a cart whose owner is deleted goes with them ──────────────────────────
    #
    # The constraint is found in the catalogue rather than named, because
    # `carts_user_id_fkey` is a name Postgres chose in migration 001 (the column
    # was declared with an inline `sa.ForeignKey` and no `name=`), not one this
    # repository ever wrote down. A database restored from a dump taken through
    # a tool that renames constraints would not have it, and a migration that
    # fails on a name is a deploy that stops halfway.
    op.execute(
        """
        DO $$
        DECLARE fk_name text;
        BEGIN
            SELECT c.conname INTO fk_name
              FROM pg_constraint c
              JOIN pg_attribute a
                ON a.attrelid = c.conrelid
               AND a.attnum = ANY (c.conkey)
             WHERE c.conrelid = 'carts'::regclass
               AND c.contype = 'f'
               AND a.attname = 'user_id'
             LIMIT 1;

            IF fk_name IS NOT NULL THEN
                EXECUTE format('ALTER TABLE carts DROP CONSTRAINT %I', fk_name);
            END IF;
        END $$
        """
    )
    op.execute(
        """
        ALTER TABLE carts
          ADD CONSTRAINT carts_user_id_fkey
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        """
    )

    # ── and one cannot be made again ──────────────────────────────────────────
    #
    # The empty string is refused alongside NULL. It is not a value any code
    # here writes, but it is what a header that arrives present-and-blank would
    # become, and `WHERE session_id = ''` would then collect session-less
    # shoppers into one basket exactly as `IS NULL` did.
    op.execute(
        """
        ALTER TABLE carts
          ADD CONSTRAINT ck_carts_has_identity
          CHECK (user_id IS NOT NULL OR (session_id IS NOT NULL AND session_id <> ''))
        """
    )


def downgrade() -> None:
    # The deleted carts are not restored: they had no identity to restore them
    # to. Everything else goes back exactly as it was.
    op.execute("ALTER TABLE carts DROP CONSTRAINT IF EXISTS ck_carts_has_identity")
    op.execute("ALTER TABLE carts DROP CONSTRAINT IF EXISTS carts_user_id_fkey")
    op.execute(
        """
        ALTER TABLE carts
          ADD CONSTRAINT carts_user_id_fkey
          FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE SET NULL
        """
    )
