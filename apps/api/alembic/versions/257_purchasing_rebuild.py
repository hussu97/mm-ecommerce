"""Rebuild the purchasing schema: VAT-deductible suppliers, contacts, PO VAT split.

The old supplier/PO tables were unused (0 purchase orders) and the supplier
record was outdated. This reshapes them for the new purchasing flow:

- ``suppliers`` gains ``is_vat_deductible`` and loses the single inline contact
  (``contact_name``/``phone``/``email``) — contacts move to their own table so a
  supplier can have several, each reachable by email or phone.
- ``supplier_contacts`` is new, with a CHECK that a contact is more than a name.
- ``supplier_items.cost`` → ``default_unit_cost`` (clearer: the prefilled price).
- ``purchase_orders`` gains ``origin`` (admin vs pos), ``supplier_reference``,
  the GCS invoice pointer, and frozen VAT totals.
- ``purchase_order_items`` gains the explicit gross / VAT / net cost split.

Additive/rename ALTERs rather than drop-and-recreate, so the FKs
``inventory_transactions``/``inventory_cost_layers`` hold onto the tables keep
working. Existing supplier rows default to VAT-deductible.

Revision ID: 257_purchasing_rebuild
Revises: 256_cost_layers
Create Date: 2026-09-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "257_purchasing_rebuild"
down_revision: Union[str, None] = "256_cost_layers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── suppliers ──────────────────────────────────────────────────────────
    op.add_column(
        "suppliers",
        sa.Column(
            "is_vat_deductible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.drop_column("suppliers", "contact_name")
    op.drop_column("suppliers", "phone")
    op.drop_column("suppliers", "email")

    # ── supplier_contacts ──────────────────────────────────────────────────
    op.create_table(
        "supplier_contacts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "supplier_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "email IS NOT NULL OR phone IS NOT NULL",
            name="ck_supplier_contact_reachable",
        ),
    )
    op.create_index(
        "ix_supplier_contacts_supplier_id", "supplier_contacts", ["supplier_id"]
    )

    # ── supplier_items ─────────────────────────────────────────────────────
    op.alter_column("supplier_items", "cost", new_column_name="default_unit_cost")

    # ── purchase_orders ────────────────────────────────────────────────────
    op.add_column(
        "purchase_orders",
        sa.Column(
            "origin", sa.String(length=10), nullable=False, server_default="admin"
        ),
    )
    op.add_column(
        "purchase_orders",
        sa.Column("supplier_reference", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "purchase_orders",
        sa.Column("invoice_object_key", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "purchase_orders",
        sa.Column("invoice_content_type", sa.String(length=100), nullable=True),
    )
    for column in ("subtotal_net", "vat_total", "total_gross"):
        op.add_column(
            "purchase_orders",
            sa.Column(column, sa.Numeric(16, 4), nullable=False, server_default="0"),
        )
    op.create_check_constraint(
        "ck_purchase_orders_origin",
        "purchase_orders",
        "origin IN ('admin', 'pos')",
    )

    # ── purchase_order_items ───────────────────────────────────────────────
    for column in ("entered_total", "vat_amount", "net_total"):
        op.add_column(
            "purchase_order_items",
            sa.Column(column, sa.Numeric(16, 4), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for column in ("net_total", "vat_amount", "entered_total"):
        op.drop_column("purchase_order_items", column)

    op.drop_constraint("ck_purchase_orders_origin", "purchase_orders", type_="check")
    for column in (
        "total_gross",
        "vat_total",
        "subtotal_net",
        "invoice_content_type",
        "invoice_object_key",
        "supplier_reference",
        "origin",
    ):
        op.drop_column("purchase_orders", column)

    op.alter_column("supplier_items", "default_unit_cost", new_column_name="cost")

    op.drop_index("ix_supplier_contacts_supplier_id", table_name="supplier_contacts")
    op.drop_table("supplier_contacts")

    op.add_column("suppliers", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column("suppliers", sa.Column("phone", sa.String(length=30), nullable=True))
    op.add_column(
        "suppliers", sa.Column("contact_name", sa.String(length=150), nullable=True)
    )
    op.drop_column("suppliers", "is_vat_deductible")
