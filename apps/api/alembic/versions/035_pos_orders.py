"""POS order engine: order/order_item POS columns, payments, charges, discounts,
taxes and kitchen tickets.

Revision ID: 035_pos_orders
Revises: 034_pos_foundation
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "035_pos_orders"
down_revision: Union[str, None] = "034_pos_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


ORDER_COLUMNS = [
    (
        "is_pos",
        sa.Column("is_pos", sa.Boolean(), nullable=False, server_default="false"),
    ),
    ("branch_id", sa.Column("branch_id", UUID, nullable=True)),
    ("table_id", sa.Column("table_id", UUID, nullable=True)),
    ("device_id", sa.Column("device_id", UUID, nullable=True)),
    ("till_id", sa.Column("till_id", UUID, nullable=True)),
    ("order_type", sa.Column("order_type", sa.String(20), nullable=True)),
    ("source", sa.Column("source", sa.String(20), nullable=True)),
    ("pos_status", sa.Column("pos_status", sa.String(20), nullable=True)),
    ("delivery_status", sa.Column("delivery_status", sa.String(20), nullable=True)),
    ("business_date", sa.Column("business_date", sa.String(10), nullable=True)),
    ("check_number", sa.Column("check_number", sa.Integer(), nullable=True)),
    ("guests", sa.Column("guests", sa.Integer(), nullable=False, server_default="1")),
    ("kitchen_notes", sa.Column("kitchen_notes", sa.Text(), nullable=True)),
    ("creator_id", sa.Column("creator_id", UUID, nullable=True)),
    ("closer_id", sa.Column("closer_id", UUID, nullable=True)),
    ("driver_id", sa.Column("driver_id", UUID, nullable=True)),
    ("customer_name", sa.Column("customer_name", sa.String(150), nullable=True)),
    ("customer_phone", sa.Column("customer_phone", sa.String(30), nullable=True)),
    (
        "charges_amount",
        sa.Column(
            "charges_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
    ),
    (
        "rounding_amount",
        sa.Column(
            "rounding_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
    ),
    (
        "tips_amount",
        sa.Column("tips_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
    ),
    ("opened_at", sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True)),
    (
        "accepted_at",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    ),
    ("due_at", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True)),
    ("closed_at", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True)),
    ("voided_at", sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True)),
    ("void_reason_id", sa.Column("void_reason_id", UUID, nullable=True)),
    ("original_order_id", sa.Column("original_order_id", UUID, nullable=True)),
]

ORDER_ITEM_COLUMNS = [
    sa.Column("status", sa.String(20), nullable=True),
    sa.Column("returned_quantity", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("discount_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
    sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
    sa.Column(
        "tax_exclusive_unit_price",
        sa.Numeric(12, 2),
        nullable=False,
        server_default="0",
    ),
    sa.Column(
        "tax_exclusive_total_price",
        sa.Numeric(12, 2),
        nullable=False,
        server_default="0",
    ),
    sa.Column("is_open_price", sa.Boolean(), nullable=False, server_default="false"),
    sa.Column("kitchen_notes", sa.Text(), nullable=True),
    sa.Column("kitchen_flow_id", UUID, nullable=True),
    sa.Column("sent_to_kitchen_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("creator_id", UUID, nullable=True),
    sa.Column("voided_by_id", UUID, nullable=True),
    sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("void_reason_id", UUID, nullable=True),
]


def upgrade() -> None:
    # ─── orders ───────────────────────────────────────────────────────────────
    for _, column in ORDER_COLUMNS:
        op.add_column("orders", column)

    op.create_foreign_key(
        "fk_orders_branch_id",
        "orders",
        "branches",
        ["branch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_orders_table_id",
        "orders",
        "tables",
        ["table_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_orders_device_id",
        "orders",
        "devices",
        ["device_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_orders_till_id", "orders", "tills", ["till_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key(
        "fk_orders_creator_id",
        "orders",
        "users",
        ["creator_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_orders_closer_id",
        "orders",
        "users",
        ["closer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_orders_driver_id",
        "orders",
        "users",
        ["driver_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_orders_void_reason_id",
        "orders",
        "reasons",
        ["void_reason_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_orders_original_order_id",
        "orders",
        "orders",
        ["original_order_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_orders_is_pos", "orders", ["is_pos"])
    op.create_index("ix_orders_branch_id", "orders", ["branch_id"])
    op.create_index("ix_orders_till_id", "orders", ["till_id"])
    op.create_index("ix_orders_order_type", "orders", ["order_type"])
    op.create_index("ix_orders_source", "orders", ["source"])
    op.create_index("ix_orders_pos_status", "orders", ["pos_status"])
    op.create_index("ix_orders_business_date", "orders", ["business_date"])
    # The POS floor view is "open checks at this branch today" — cover it directly.
    op.create_index(
        "ix_orders_branch_business_date_pos_status",
        "orders",
        ["branch_id", "business_date", "pos_status"],
    )

    # ─── order_items ──────────────────────────────────────────────────────────
    for column in ORDER_ITEM_COLUMNS:
        op.add_column("order_items", column)

    op.create_foreign_key(
        "fk_order_items_kitchen_flow_id",
        "order_items",
        "kitchen_flows",
        ["kitchen_flow_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_order_items_creator_id",
        "order_items",
        "users",
        ["creator_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_order_items_voided_by_id",
        "order_items",
        "users",
        ["voided_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_order_items_void_reason_id",
        "order_items",
        "reasons",
        ["void_reason_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_order_items_status", "order_items", ["status"])

    # ─── order_payments ───────────────────────────────────────────────────────
    op.create_table(
        "order_payments",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("payment_method_id", UUID, nullable=False),
        sa.Column("till_id", UUID, nullable=True),
        sa.Column("user_id", UUID, nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("tendered", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("tips", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "change_given", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("is_refund", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reference", sa.String(120), nullable=True),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["payment_method_id"], ["payment_methods.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["till_id"], ["tills.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_payments_order_id", "order_payments", ["order_id"])
    op.create_index(
        "ix_order_payments_payment_method_id", "order_payments", ["payment_method_id"]
    )
    op.create_index(
        "ix_order_payments_business_date", "order_payments", ["business_date"]
    )

    # ─── order_charges ────────────────────────────────────────────────────────
    op.create_table(
        "order_charges",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("charge_id", UUID, nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column(
            "tax_exclusive_amount",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["charge_id"], ["charges.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_charges_order_id", "order_charges", ["order_id"])

    # ─── order_discounts ──────────────────────────────────────────────────────
    op.create_table(
        "order_discounts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("order_item_id", UUID, nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("reference_id", UUID, nullable=True),
        sa.Column(
            "is_percentage", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("value", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("applied_by_id", UUID, nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["order_items.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["applied_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_order_discounts_order_id", "order_discounts", ["order_id"])
    op.create_index(
        "ix_order_discounts_order_item_id", "order_discounts", ["order_item_id"]
    )
    op.create_index("ix_order_discounts_source", "order_discounts", ["source"])

    # ─── order_taxes ──────────────────────────────────────────────────────────
    op.create_table(
        "order_taxes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("tax_id", UUID, nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("rate", sa.Numeric(6, 4), nullable=False),
        sa.Column(
            "taxable_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tax_id"], ["taxes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", "tax_id", name="uq_order_tax"),
    )
    op.create_index("ix_order_taxes_order_id", "order_taxes", ["order_id"])

    # ─── kitchen tickets ──────────────────────────────────────────────────────
    op.create_table(
        "kitchen_tickets",
        sa.Column("id", UUID, nullable=False),
        sa.Column("order_id", UUID, nullable=False),
        sa.Column("kitchen_flow_id", UUID, nullable=True),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reprint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["kitchen_flow_id"], ["kitchen_flows.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kitchen_tickets_order_id", "kitchen_tickets", ["order_id"])
    op.create_index(
        "ix_kitchen_tickets_kitchen_flow_id", "kitchen_tickets", ["kitchen_flow_id"]
    )
    op.create_index("ix_kitchen_tickets_branch_id", "kitchen_tickets", ["branch_id"])
    op.create_index("ix_kitchen_tickets_status", "kitchen_tickets", ["status"])

    op.create_table(
        "kitchen_ticket_items",
        sa.Column("id", UUID, nullable=False),
        sa.Column("ticket_id", UUID, nullable=False),
        sa.Column("order_item_id", UUID, nullable=True),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("modifiers_summary", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="new"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["kitchen_tickets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["order_item_id"], ["order_items.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_kitchen_ticket_items_ticket_id", "kitchen_ticket_items", ["ticket_id"]
    )
    op.create_index(
        "ix_kitchen_ticket_items_order_item_id",
        "kitchen_ticket_items",
        ["order_item_id"],
    )


def downgrade() -> None:
    op.drop_table("kitchen_ticket_items")
    op.drop_table("kitchen_tickets")
    op.drop_table("order_taxes")
    op.drop_table("order_discounts")
    op.drop_table("order_charges")
    op.drop_table("order_payments")

    op.drop_index("ix_order_items_status", table_name="order_items")
    for name in (
        "fk_order_items_void_reason_id",
        "fk_order_items_voided_by_id",
        "fk_order_items_creator_id",
        "fk_order_items_kitchen_flow_id",
    ):
        op.drop_constraint(name, "order_items", type_="foreignkey")
    for column in reversed(ORDER_ITEM_COLUMNS):
        op.drop_column("order_items", column.name)

    for name in (
        "ix_orders_branch_business_date_pos_status",
        "ix_orders_business_date",
        "ix_orders_pos_status",
        "ix_orders_source",
        "ix_orders_order_type",
        "ix_orders_till_id",
        "ix_orders_branch_id",
        "ix_orders_is_pos",
    ):
        op.drop_index(name, table_name="orders")
    for name in (
        "fk_orders_original_order_id",
        "fk_orders_void_reason_id",
        "fk_orders_driver_id",
        "fk_orders_closer_id",
        "fk_orders_creator_id",
        "fk_orders_till_id",
        "fk_orders_device_id",
        "fk_orders_table_id",
        "fk_orders_branch_id",
    ):
        op.drop_constraint(name, "orders", type_="foreignkey")
    for name, _ in reversed(ORDER_COLUMNS):
        op.drop_column("orders", name)
