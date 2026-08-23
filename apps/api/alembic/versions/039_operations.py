"""Transfer orders, spot checks, reservations, notification rules, and the
customer blacklist flag.

Closes the last verified gaps against the Foodics feature audit
(see docs/foodics-coverage-matrix.md).

Revision ID: 039_operations
Revises: 038_menu_marketing
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "039_operations"
down_revision: Union[str, None] = "038_menu_marketing"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "transfer_orders",
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("warehouse_id", UUID, nullable=True),
        sa.Column("source_branch_id", UUID, nullable=False),
        sa.Column("source_warehouse_id", UUID, nullable=True),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("required_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("creator_id", UUID, nullable=True),
        sa.Column("submitter_id", UUID, nullable=True),
        sa.Column("responder_id", UUID, nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_transaction_id", UUID, nullable=True),
        sa.Column("received_transaction_id", UUID, nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["source_branch_id"], ["branches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_warehouse_id"], ["warehouses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitter_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["responder_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["sent_transaction_id"], ["inventory_transactions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["received_transaction_id"],
            ["inventory_transactions.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transfer_orders_reference", "transfer_orders", ["reference"], unique=True
    )
    op.create_index(
        "ix_transfer_orders_status", "transfer_orders", ["status"], unique=False
    )
    op.create_index(
        "ix_transfer_orders_branch_id", "transfer_orders", ["branch_id"], unique=False
    )
    op.create_index(
        "ix_transfer_orders_source_branch_id",
        "transfer_orders",
        ["source_branch_id"],
        unique=False,
    )
    op.create_index(
        "ix_transfer_orders_business_date",
        "transfer_orders",
        ["business_date"],
        unique=False,
    )

    op.create_table(
        "transfer_order_items",
        sa.Column("transfer_order_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column("approved_quantity", sa.Numeric(16, 4), nullable=True),
        sa.Column(
            "sent_quantity", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "received_quantity", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column("unit", sa.String(30), nullable=False, server_default="storage"),
        sa.Column(
            "conversion_factor", sa.Numeric(16, 6), nullable=False, server_default="1"
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["transfer_order_id"], ["transfer_orders.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_transfer_order_items_transfer_order_id",
        "transfer_order_items",
        ["transfer_order_id"],
        unique=False,
    )
    op.create_index(
        "ix_transfer_order_items_item_id",
        "transfer_order_items",
        ["item_id"],
        unique=False,
    )

    op.create_table(
        "spot_checks",
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("warehouse_id", UUID, nullable=True),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("creator_id", UUID, nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_spot_checks_reference", "spot_checks", ["reference"], unique=True
    )
    op.create_index(
        "ix_spot_checks_branch_id", "spot_checks", ["branch_id"], unique=False
    )
    op.create_index(
        "ix_spot_checks_business_date", "spot_checks", ["business_date"], unique=False
    )
    op.create_index("ix_spot_checks_status", "spot_checks", ["status"], unique=False)

    op.create_table(
        "spot_check_items",
        sa.Column("spot_check_id", UUID, nullable=False),
        sa.Column("item_id", UUID, nullable=False),
        sa.Column("counted_quantity", sa.Numeric(16, 4), nullable=False),
        sa.Column(
            "system_quantity", sa.Numeric(16, 4), nullable=False, server_default="0"
        ),
        sa.Column("variance", sa.Numeric(16, 4), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["spot_check_id"], ["spot_checks.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["inventory_items.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_spot_check_items_spot_check_id",
        "spot_check_items",
        ["spot_check_id"],
        unique=False,
    )
    op.create_index(
        "ix_spot_check_items_item_id", "spot_check_items", ["item_id"], unique=False
    )

    op.create_table(
        "reservations",
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("table_id", UUID, nullable=True),
        sa.Column("customer_id", UUID, nullable=True),
        sa.Column("customer_name", sa.String(150), nullable=False),
        sa.Column("customer_phone", sa.String(30), nullable=True),
        sa.Column("guests", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "duration_minutes", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("created_by_id", UUID, nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["table_id"], ["tables.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["customer_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_reservations_reference", "reservations", ["reference"], unique=True
    )
    op.create_index(
        "ix_reservations_branch_id", "reservations", ["branch_id"], unique=False
    )
    op.create_index(
        "ix_reservations_starts_at", "reservations", ["starts_at"], unique=False
    )
    op.create_index("ix_reservations_status", "reservations", ["status"], unique=False)

    op.create_table(
        "notification_rules",
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("event", sa.String(60), nullable=False),
        sa.Column("threshold", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "branch_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column(
            "recipient_user_ids",
            postgresql.ARRAY(UUID),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "recipient_emails",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "channels",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{email}",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("meta", JSONB, nullable=False, server_default="{}"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", UUID, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notification_rules_event", "notification_rules", ["event"], unique=False
    )

    op.add_column(
        "users",
        sa.Column(
            "is_blacklisted", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column("users", sa.Column("blacklist_reason", sa.String(255), nullable=True))
    op.create_index("ix_users_is_blacklisted", "users", ["is_blacklisted"])


def downgrade() -> None:
    op.drop_index("ix_users_is_blacklisted", table_name="users")
    op.drop_column("users", "blacklist_reason")
    op.drop_column("users", "is_blacklisted")
    op.drop_table("notification_rules")
    op.drop_table("reservations")
    op.drop_table("spot_check_items")
    op.drop_table("spot_checks")
    op.drop_table("transfer_order_items")
    op.drop_table("transfer_orders")
