"""POS foundation: branches, taxes, payment methods, charges, reasons, tags,
kitchen flows, devices/printers, sections/tables, tills/shifts/drawer operations,
roles & staff users, business settings.

Revision ID: 034_pos_foundation
Revises: 033_set_admin_initial_passwords
Create Date: 2026-07-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "034_pos_foundation"
down_revision: Union[str, None] = "033_set_admin_initial_passwords"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    # ─── Taxes ────────────────────────────────────────────────────────────────
    op.create_table(
        "taxes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("rate", sa.Numeric(6, 4), nullable=False),
        sa.Column("type", sa.String(20), nullable=False, server_default="inclusive"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "tax_groups",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tax_groups_reference", "tax_groups", ["reference"], unique=True)

    op.create_table(
        "tax_group_taxes",
        sa.Column("id", UUID, nullable=False),
        sa.Column("tax_group_id", UUID, nullable=False),
        sa.Column("tax_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["tax_group_id"], ["tax_groups.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tax_id"], ["taxes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tax_group_id", "tax_id", name="uq_tax_group_tax"),
    )
    op.create_index(
        "ix_tax_group_taxes_tax_group_id", "tax_group_taxes", ["tax_group_id"]
    )
    op.create_index("ix_tax_group_taxes_tax_id", "tax_group_taxes", ["tax_id"])

    # ─── Roles ────────────────────────────────────────────────────────────────
    op.create_table(
        "roles",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "permissions",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "is_super_admin", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    # ─── Branches ─────────────────────────────────────────────────────────────
    op.create_table(
        "branches",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("name_localized", sa.String(200), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("type", sa.String(30), nullable=False, server_default="restaurant"),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("phone", sa.String(30), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("opening_from", sa.String(5), nullable=False, server_default="00:00"),
        sa.Column("opening_to", sa.String(5), nullable=False, server_default="23:59"),
        sa.Column(
            "business_day_start", sa.String(5), nullable=False, server_default="04:00"
        ),
        sa.Column(
            "inventory_end_of_day_time",
            sa.String(5),
            nullable=False,
            server_default="04:00",
        ),
        sa.Column("receipt_header", sa.Text(), nullable=True),
        sa.Column("receipt_footer", sa.Text(), nullable=True),
        sa.Column("tax_number", sa.String(50), nullable=True),
        sa.Column("tax_registration_name", sa.String(200), nullable=True),
        sa.Column("tax_group_id", UUID, nullable=True),
        sa.Column(
            "receives_online_orders",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "accepts_reservations", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "reservation_duration", sa.Integer(), nullable=False, server_default="60"
        ),
        sa.Column("reservation_times", JSONB, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tax_group_id"], ["tax_groups.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_branches_reference", "branches", ["reference"], unique=True)
    op.create_index("ix_branches_is_active", "branches", ["is_active"])

    op.create_table(
        "branch_business_days",
        sa.Column("id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_by_id", UUID, nullable=True),
        sa.Column("closed_by_id", UUID, nullable=True),
        sa.Column("total_sales", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("total_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "total_discounts", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_returns", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("total_taxes", sa.Numeric(12, 2), nullable=False, server_default="0"),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["opened_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["closed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "branch_id", "business_date", name="uq_branch_business_date"
        ),
    )
    op.create_index(
        "ix_branch_business_days_branch_id", "branch_business_days", ["branch_id"]
    )
    op.create_index(
        "ix_branch_business_days_business_date",
        "branch_business_days",
        ["business_date"],
    )

    # ─── Staff columns on users ───────────────────────────────────────────────
    op.add_column(
        "users",
        sa.Column("is_staff", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("users", sa.Column("display_name", sa.String(150), nullable=True))
    op.add_column("users", sa.Column("staff_number", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("pin_hash", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("role_id", UUID, nullable=True))
    op.add_column(
        "users",
        sa.Column("is_driver", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_foreign_key(
        "fk_users_role_id", "users", "roles", ["role_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index("ix_users_staff_number", "users", ["staff_number"], unique=True)
    op.create_index("ix_users_role_id", "users", ["role_id"])

    op.create_table(
        "user_branches",
        sa.Column("id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "branch_id", name="uq_user_branch"),
    )
    op.create_index("ix_user_branches_user_id", "user_branches", ["user_id"])
    op.create_index("ix_user_branches_branch_id", "user_branches", ["branch_id"])

    # ─── Payment methods ──────────────────────────────────────────────────────
    op.create_table(
        "payment_methods",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column(
            "auto_open_drawer", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "allows_tendering", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("allows_tips", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("allows_refund", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("rounding_step", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payment_methods_code", "payment_methods", ["code"], unique=True)
    op.create_index("ix_payment_methods_type", "payment_methods", ["type"])
    op.create_index("ix_payment_methods_is_active", "payment_methods", ["is_active"])

    # ─── Charges ──────────────────────────────────────────────────────────────
    op.create_table(
        "charges",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("reference", sa.String(50), nullable=True),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("value", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column(
            "is_auto_applied", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "order_types",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("tax_group_id", UUID, nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tax_group_id"], ["tax_groups.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_charges_reference", "charges", ["reference"], unique=True)

    # ─── Reasons ──────────────────────────────────────────────────────────────
    op.create_table(
        "reasons",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("name_localized", sa.String(150), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_reasons_type", "reasons", ["type"])

    # ─── Tags ─────────────────────────────────────────────────────────────────
    op.create_table(
        "tags",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_localized", sa.String(100), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("color", sa.String(9), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("type", "name", name="uq_tag_type_name"),
    )
    op.create_index("ix_tags_type", "tags", ["type"])

    op.create_table(
        "tagged_entities",
        sa.Column("id", UUID, nullable=False),
        sa.Column("tag_id", UUID, nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("entity_id", UUID, nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tag_id", "entity_type", "entity_id", name="uq_tagged_entity"
        ),
    )
    op.create_index("ix_tagged_entities_tag_id", "tagged_entities", ["tag_id"])
    op.create_index(
        "ix_tagged_entities_entity_type", "tagged_entities", ["entity_type"]
    )
    op.create_index("ix_tagged_entities_entity_id", "tagged_entities", ["entity_id"])

    # ─── Kitchen flows ────────────────────────────────────────────────────────
    op.create_table(
        "kitchen_flows",
        sa.Column("id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_localized", sa.String(120), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "order_types",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "auto_complete_seconds", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kitchen_flows_branch_id", "kitchen_flows", ["branch_id"])

    op.create_table(
        "kitchen_flow_categories",
        sa.Column("id", UUID, nullable=False),
        sa.Column("kitchen_flow_id", UUID, nullable=False),
        sa.Column("category_id", UUID, nullable=False),
        sa.ForeignKeyConstraint(
            ["kitchen_flow_id"], ["kitchen_flows.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["category_id"], ["categories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "kitchen_flow_id", "category_id", name="uq_kitchen_flow_category"
        ),
    )
    op.create_index(
        "ix_kitchen_flow_categories_kitchen_flow_id",
        "kitchen_flow_categories",
        ["kitchen_flow_id"],
    )
    op.create_index(
        "ix_kitchen_flow_categories_category_id",
        "kitchen_flow_categories",
        ["category_id"],
    )

    # ─── Devices & printers ───────────────────────────────────────────────────
    op.create_table(
        "devices",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="available"),
        sa.Column("pairing_code", sa.String(12), nullable=True),
        sa.Column("pairing_code_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_hash", sa.String(255), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("app_version", sa.String(30), nullable=True),
        sa.Column("os_version", sa.String(30), nullable=True),
        sa.Column("model_identifier", sa.String(60), nullable=True),
        sa.Column(
            "category_ids", postgresql.ARRAY(UUID), nullable=False, server_default="{}"
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_devices_reference", "devices", ["reference"], unique=True)
    op.create_index("ix_devices_type", "devices", ["type"])
    op.create_index("ix_devices_branch_id", "devices", ["branch_id"])
    op.create_index("ix_devices_pairing_code", "devices", ["pairing_code"], unique=True)
    op.create_index("ix_devices_token_hash", "devices", ["token_hash"], unique=True)

    op.create_table(
        "printers",
        sa.Column("id", UUID, nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("device_id", UUID, nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="receipt"),
        sa.Column("connection", sa.String(20), nullable=False, server_default="lan"),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("port", sa.Integer(), nullable=False, server_default="9100"),
        sa.Column("identifier", sa.String(120), nullable=True),
        sa.Column("paper_width_mm", sa.Integer(), nullable=False, server_default="80"),
        sa.Column(
            "characters_per_line", sa.Integer(), nullable=False, server_default="48"
        ),
        sa.Column("codepage", sa.String(20), nullable=False, server_default="cp864"),
        sa.Column(
            "supports_arabic", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "cut_after_print", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "has_cash_drawer", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("copies", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("kitchen_flow_id", UUID, nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["kitchen_flow_id"], ["kitchen_flows.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_printers_branch_id", "printers", ["branch_id"])
    op.create_index("ix_printers_device_id", "printers", ["device_id"])

    # ─── Sections & tables ────────────────────────────────────────────────────
    op.create_table(
        "sections",
        sa.Column("id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("name_localized", sa.String(120), nullable=True),
        sa.Column("translations", JSONB, nullable=False, server_default="{}"),
        sa.Column("layout", JSONB, nullable=False, server_default="{}"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sections_branch_id", "sections", ["branch_id"])

    op.create_table(
        "tables",
        sa.Column("id", UUID, nullable=False),
        sa.Column("section_id", UUID, nullable=False),
        sa.Column("name", sa.String(60), nullable=False),
        sa.Column("seats", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("status", sa.String(20), nullable=False, server_default="free"),
        sa.Column(
            "accepts_reservations", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column("parent_id", UUID, nullable=True),
        sa.Column("revenue_center_tag_id", UUID, nullable=True),
        sa.Column("layout", JSONB, nullable=False, server_default="{}"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["section_id"], ["sections.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_id"], ["tables.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["revenue_center_tag_id"], ["tags.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tables_section_id", "tables", ["section_id"])
    op.create_index("ix_tables_status", "tables", ["status"])

    # ─── Tills, shifts, drawer operations ─────────────────────────────────────
    op.create_table(
        "tills",
        sa.Column("id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("device_id", UUID, nullable=True),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_by_id", UUID, nullable=True),
        sa.Column(
            "opening_amount", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("closing_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column(
            "estimated_cash", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("variance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("totals", JSONB, nullable=False, server_default="{}"),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["closed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tills_branch_id", "tills", ["branch_id"])
    op.create_index("ix_tills_device_id", "tills", ["device_id"])
    op.create_index("ix_tills_user_id", "tills", ["user_id"])
    op.create_index("ix_tills_business_date", "tills", ["business_date"])
    op.create_index("ix_tills_status", "tills", ["status"])

    op.create_table(
        "shifts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("branch_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("clocked_in_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clocked_out_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_shifts_branch_id", "shifts", ["branch_id"])
    op.create_index("ix_shifts_user_id", "shifts", ["user_id"])
    op.create_index("ix_shifts_business_date", "shifts", ["business_date"])

    op.create_table(
        "drawer_operations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("till_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("reason_id", UUID, nullable=True),
        sa.Column("order_id", UUID, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["till_id"], ["tills.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reason_id"], ["reasons.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_drawer_operations_till_id", "drawer_operations", ["till_id"])
    op.create_index("ix_drawer_operations_user_id", "drawer_operations", ["user_id"])
    op.create_index("ix_drawer_operations_type", "drawer_operations", ["type"])

    # ─── Business settings (singleton) ────────────────────────────────────────
    op.create_table(
        "business_settings",
        sa.Column("id", UUID, nullable=False),
        sa.Column(
            "business_name",
            sa.String(200),
            nullable=False,
            server_default="Melting Moments",
        ),
        sa.Column("logo_url", sa.String(500), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False, server_default="AED"),
        sa.Column(
            "currency_symbol", sa.String(8), nullable=False, server_default="AED"
        ),
        sa.Column("decimal_places", sa.Integer(), nullable=False, server_default="2"),
        sa.Column(
            "timezone", sa.String(64), nullable=False, server_default="Asia/Dubai"
        ),
        sa.Column("receipt_logo_url", sa.String(500), nullable=True),
        sa.Column(
            "receipt_language_mode",
            sa.String(20),
            nullable=False,
            server_default="main",
        ),
        sa.Column(
            "receipt_main_language", sa.String(5), nullable=False, server_default="en"
        ),
        sa.Column(
            "receipt_localized_language",
            sa.String(5),
            nullable=False,
            server_default="ar",
        ),
        sa.Column("receipt_header", sa.Text(), nullable=True),
        sa.Column("receipt_footer", sa.Text(), nullable=True),
        sa.Column(
            "invoice_title",
            sa.String(120),
            nullable=False,
            server_default="Tax Invoice",
        ),
        sa.Column(
            "receipt_show_order_number",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "receipt_show_calories",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "receipt_show_subtotal", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "receipt_show_rounding", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "receipt_show_closer_username",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "receipt_show_creator_username",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "receipt_show_check_number",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "receipt_hide_free_modifiers",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "receipt_show_pickup_phone",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "receipt_show_qr", sa.Boolean(), nullable=False, server_default="true"
        ),
        sa.Column(
            "kitchen_sorting", sa.String(20), nullable=False, server_default="as_added"
        ),
        sa.Column(
            "kitchen_show_default_modifiers",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "kitchen_auto_print_on_send",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column("inventory_logo_url", sa.String(500), nullable=True),
        sa.Column("inventory_header", sa.Text(), nullable=True),
        sa.Column("inventory_footer", sa.Text(), nullable=True),
        sa.Column(
            "prevent_negative_stock",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "require_customer_for_delivery",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "default_order_type", sa.String(20), nullable=False, server_default="pickup"
        ),
        sa.Column(
            "cash_rounding_step", sa.Numeric(6, 3), nullable=False, server_default="0"
        ),
        sa.Column(
            "auto_logout_seconds", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "order_number_reset_daily",
            sa.Boolean(),
            nullable=False,
            server_default="true",
        ),
        sa.Column("enable_tips", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("extra", JSONB, nullable=False, server_default="{}"),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("business_settings")
    op.drop_table("drawer_operations")
    op.drop_table("shifts")
    op.drop_table("tills")
    op.drop_table("tables")
    op.drop_table("sections")
    op.drop_table("printers")
    op.drop_table("devices")
    op.drop_table("kitchen_flow_categories")
    op.drop_table("kitchen_flows")
    op.drop_table("tagged_entities")
    op.drop_table("tags")
    op.drop_table("reasons")
    op.drop_table("charges")
    op.drop_table("payment_methods")
    op.drop_table("user_branches")

    op.drop_index("ix_users_role_id", table_name="users")
    op.drop_index("ix_users_staff_number", table_name="users")
    op.drop_constraint("fk_users_role_id", "users", type_="foreignkey")
    op.drop_column("users", "is_driver")
    op.drop_column("users", "role_id")
    op.drop_column("users", "pin_hash")
    op.drop_column("users", "staff_number")
    op.drop_column("users", "display_name")
    op.drop_column("users", "is_staff")

    op.drop_table("branch_business_days")
    op.drop_table("branches")
    op.drop_table("roles")
    op.drop_table("tax_group_taxes")
    op.drop_table("tax_groups")
    op.drop_table("taxes")
