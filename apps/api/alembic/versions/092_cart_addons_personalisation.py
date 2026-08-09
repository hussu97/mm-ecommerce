"""A product can be an add-on the cart offers, and can carry a note somebody writes by hand.

The storefront's USP marquee has been advertising "gift boxes with a handwritten
note" to every visitor, and there was nowhere in the schema to put the note. A
customer who came for exactly that reached checkout with no way to say who the
box was from.

Two capabilities, deliberately kept apart rather than folded into one flag:

* `is_cart_addon` — whether the cart offers this product in its add-on tray.
* `personalisation_type` — whether this product captures text, and what kind.

They are not the same question. A candle is a tray item with nothing to write; an
engraved plaque captures text without belonging in a dessert cart's tray. One
column each means the next add-on is configuration rather than another migration.
`personalisation_type` is a string and not a boolean for the same reason — a
second kind of personalisation should be a value, not a schema change.

`personalisation_max_length` is per product because the ceiling is a physical
fact about the card being written on, not a constant about our software. The
default of 100 is what fits the current gift tag.

The note lands on `cart_items` and again on `order_items`. On the order it is a
**new column rather than `kitchen_notes`**, which the register writes when a
cashier types an instruction. Sharing one column would let a cashier's "no nuts"
and a customer's "Happy birthday, Sara" arrive as the same string with no way to
tell which is which — and both of them get printed.

Revision ID: 092_cart_addons_personalisation
Revises: 091_order_status_events
Create Date: 2026-08-09

`alembic_version.version_num` is `varchar(32)`, so a revision id is not free to
be as descriptive as it likes — the "and" this one started with put it at 35 and
the upgrade failed on its own bookkeeping *after* running every statement.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "092_cart_addons_personalisation"
down_revision: Union[str, None] = "091_order_status_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "is_cart_addon",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "products",
        sa.Column("personalisation_type", sa.String(30), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column(
            "personalisation_max_length",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
    )

    # The tray asks for exactly one thing on every cart render: the handful of
    # products flagged as add-ons. A partial index keeps that lookup off a table
    # scan of the whole catalogue, and stays tiny because almost no row
    # qualifies.
    op.create_index(
        "ix_products_cart_addon",
        "products",
        ["is_cart_addon"],
        unique=False,
        postgresql_where=sa.text("is_cart_addon"),
    )

    op.add_column(
        "cart_items",
        sa.Column("personalisation_note", sa.Text(), nullable=True),
    )
    op.add_column(
        "order_items",
        sa.Column("personalisation_note", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order_items", "personalisation_note")
    op.drop_column("cart_items", "personalisation_note")
    op.drop_index("ix_products_cart_addon", table_name="products")
    op.drop_column("products", "personalisation_max_length")
    op.drop_column("products", "personalisation_type")
    op.drop_column("products", "is_cart_addon")
