"""Custom orders v2: a custom order is an `orders` row with `source = 'custom'`.

- The capacity calendar goes: ``custom_orders`` (the diary) and
  ``custom_order_blackouts`` are dropped. Both were empty in production when
  this was written (2026-09-26), and no request path read their rows once the
  code that did was removed in the same change.
- ``custom_orders`` returns as a 1:1 extension of ``orders`` (payment type, card
  fee mode, the enquiry it came from, when its docket was printed), and
  ``custom_order_recipe_lines`` holds each order's own recipe.
- ``orders.source`` gets the CHECK it never had, with ``custom`` added.
- ``orders.delivered_at`` (backfilled from the status trail) and the generated
  ``orders.reporting_at`` — the one definition of "which day does this order
  count on" that every date-bucketed report reads.
- ``production_orders.origin`` tells POS-raised custom-cake production apart.
- ``email_logs.cc`` journals copied addresses.

The capacity columns on ``business_settings`` and ``products`` are left in place
here and dropped by a later migration: the deploy migrates before it cuts
traffic over, and the container still serving maps them.

Revision ID: 294_custom_orders_v2
Revises: 293_po_misc_categories
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = "294_custom_orders_v2"
down_revision: Union[str, None] = "293_po_misc_categories"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REPORTING_AT = (
    "CASE WHEN source = 'custom' "
    "THEN COALESCE(delivered_at, promised_at, created_at) "
    "ELSE created_at END"
)


def upgrade() -> None:
    # ── The old diary. Refuse rather than destroy a booking somebody made. ──
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM custom_orders)
               OR EXISTS (SELECT 1 FROM custom_order_blackouts) THEN
                RAISE EXCEPTION
                    'custom_orders / custom_order_blackouts hold rows; '
                    'migrate them before dropping the capacity calendar';
            END IF;
        END $$;
        """
    )
    op.drop_table("custom_order_blackouts")
    op.drop_table("custom_orders")

    # ── The new extension table + per-order recipe. ─────────────────────────
    op.create_table(
        "custom_orders",
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("payment_type", sa.String(20), nullable=True),
        sa.Column("card_fee_mode", sa.String(20), nullable=True),
        sa.Column(
            "enquiry_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_order_enquiries.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("created_via", sa.String(10), nullable=False),
        sa.Column("kitchen_printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "payment_type IS NULL OR payment_type IN ('bank_transfer', 'card', 'cash')",
            name="ck_custom_orders_payment_type_allowed",
        ),
        sa.CheckConstraint(
            "card_fee_mode IS NULL OR card_fee_mode IN ('separate_line', 'included')",
            name="ck_custom_orders_card_fee_mode_allowed",
        ),
        sa.CheckConstraint(
            "created_via IN ('admin', 'pos')",
            name="ck_custom_orders_created_via_allowed",
        ),
        sa.CheckConstraint(
            "(payment_type IS NOT DISTINCT FROM 'card') = (card_fee_mode IS NOT NULL)",
            name="ck_custom_orders_card_fee_mode_iff_card",
        ),
    )
    op.create_index(
        "ix_custom_orders_created_by_id", "custom_orders", ["created_by_id"]
    )

    op.create_table(
        "custom_order_recipe_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("custom_orders.order_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "order_id", "item_id", name="uq_custom_order_recipe_lines_order_item"
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_custom_order_recipe_lines_quantity"
        ),
    )
    op.create_index(
        "ix_custom_order_recipe_lines_order_id",
        "custom_order_recipe_lines",
        ["order_id"],
    )
    op.create_index(
        "ix_custom_order_recipe_lines_item_id",
        "custom_order_recipe_lines",
        ["item_id"],
    )

    # ── orders.source: the CHECK it never had. ───────────────────────────────
    op.create_check_constraint(
        "ck_orders_source_allowed",
        "orders",
        "source IN ('cashier', 'online', 'aggregator', 'custom')",
    )

    # ── delivered_at, from the status trail (latest arrival at delivered). ───
    op.add_column(
        "orders", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE orders o
        SET delivered_at = e.at
        FROM (
            SELECT order_id, max(at) AS at
            FROM order_status_events
            WHERE status = 'delivered'
            GROUP BY order_id
        ) e
        WHERE e.order_id = o.id
          AND o.status = 'delivered'
        """
    )

    # ── reporting_at: generated, stored, indexed. Rewrites the table. ───────
    op.execute(
        f"ALTER TABLE orders ADD COLUMN reporting_at timestamptz "
        f"GENERATED ALWAYS AS ({_REPORTING_AT}) STORED"
    )
    op.create_index("ix_orders_reporting_at", "orders", ["reporting_at"])

    # ── production_orders.origin ─────────────────────────────────────────────
    op.add_column(
        "production_orders",
        sa.Column("origin", sa.String(10), nullable=False, server_default="admin"),
    )
    op.create_check_constraint(
        "ck_production_orders_origin_allowed",
        "production_orders",
        "origin IN ('admin', 'pos')",
    )

    # ── email_logs.cc ────────────────────────────────────────────────────────
    op.add_column("email_logs", sa.Column("cc", ARRAY(sa.String(255)), nullable=True))


def downgrade() -> None:
    op.drop_column("email_logs", "cc")
    op.drop_constraint(
        "ck_production_orders_origin_allowed", "production_orders", type_="check"
    )
    op.drop_column("production_orders", "origin")
    op.drop_index("ix_orders_reporting_at", table_name="orders")
    op.drop_column("orders", "reporting_at")
    op.drop_column("orders", "delivered_at")
    op.drop_constraint("ck_orders_source_allowed", "orders", type_="check")

    # A downgrade on a database that has taken custom orders would strand them
    # as `orders` rows with an unknown source; refuse instead.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM custom_orders) THEN
                RAISE EXCEPTION
                    'custom orders exist; they cannot be downgraded to the '
                    'capacity calendar';
            END IF;
        END $$;
        """
    )
    op.drop_table("custom_order_recipe_lines")
    op.drop_table("custom_orders")

    # The capacity calendar as migrations 081/099/138/272 left it.
    op.create_table(
        "custom_orders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="enquiry"),
        sa.Column("source", sa.String(20), nullable=False, server_default="website"),
        sa.Column(
            "order_id",
            UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("customer_name", sa.String(150), nullable=False),
        sa.Column("customer_phone", sa.String(30), nullable=True),
        sa.Column("customer_phone_country", sa.String(2), nullable=True),
        sa.Column("customer_email", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("cake_message", sa.String(200), nullable=True),
        sa.Column("flavour", sa.String(120), nullable=True),
        sa.Column("size_label", sa.String(60), nullable=True),
        sa.Column("servings", sa.Integer(), nullable=True),
        sa.Column(
            "reference_image_urls",
            ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("quoted_total", sa.Numeric(10, 2), nullable=True),
        sa.Column(
            "deposit_amount", sa.Numeric(10, 2), nullable=False, server_default="0"
        ),
        sa.Column("deposit_paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "brief",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column(
            "created_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('enquiry', 'confirmed', 'in_production', 'ready', "
            "'completed', 'cancelled')",
            name="ck_custom_orders_status_allowed",
        ),
        sa.CheckConstraint(
            "source IN ('website', 'instagram', 'whatsapp', 'phone', 'walk_in', "
            "'other')",
            name="ck_custom_orders_source_allowed",
        ),
    )
    op.create_index("ix_custom_orders_due_date", "custom_orders", ["due_date"])
    op.create_index("ix_custom_orders_status", "custom_orders", ["status"])
    op.create_index("ix_custom_orders_order_id", "custom_orders", ["order_id"])
    op.create_index("ix_custom_orders_branch_id", "custom_orders", ["branch_id"])
    op.create_index(
        "ix_custom_orders_due_date_status", "custom_orders", ["due_date", "status"]
    )
    op.create_table(
        "custom_order_blackouts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("blackout_date", sa.Date(), nullable=False, unique=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_custom_order_blackouts_blackout_date",
        "custom_order_blackouts",
        ["blackout_date"],
    )
