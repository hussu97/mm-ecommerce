"""Version inventory transfer templates (append-only) and snapshot them onto orders.

Transfer templates gain the same append-only versioning as shift-report templates:

1. ``inventory_transfer_templates.version_number`` — an integer revision within a
   ``(source_branch_id, name)`` lineage. Existing rows are numbered per lineage by
   ``created_at, id`` so a lineage that already had two same-named rows does not
   collide, then a unique constraint over ``(source_branch_id, name,
   version_number)`` becomes the database backstop for the service's allocation.
2. ``inventory_transfer_template_items`` gains a ``(template_id, item_id)`` unique
   constraint (any pre-existing duplicate is collapsed to its lowest id first).
3. ``transfer_orders`` gains a nullable template snapshot — ``template_id`` (FK
   RESTRICT), ``template_version`` and ``template_snapshot`` — so a transfer raised
   from a template records the template as it stood, immune to later edits. Nullable
   because returns and ad-hoc transfers are raised without a template.

Revision ID: 226_transfer_template_versions
Revises: 225_inventory_accounting_fixes
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "226_transfer_template_versions"
down_revision: Union[str, None] = "225_inventory_accounting_fixes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. version_number on the templates. Default 1 for existing rows, then number
    #    each lineage's rows so no two rows in a (source_branch_id, name) lineage
    #    share a version before the unique constraint is added.
    op.add_column(
        "inventory_transfer_templates",
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
    )
    op.execute(
        """
        UPDATE inventory_transfer_templates AS t
        SET version_number = seq.rn
        FROM (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY source_branch_id, name
                       ORDER BY created_at, id
                   ) AS rn
            FROM inventory_transfer_templates
        ) AS seq
        WHERE t.id = seq.id
        """
    )
    op.create_unique_constraint(
        "uq_inventory_transfer_template_revision",
        "inventory_transfer_templates",
        ["source_branch_id", "name", "version_number"],
    )

    # 2. Item uniqueness. Collapse any pre-existing (template_id, item_id) duplicate
    #    to its lowest id so the constraint can be added.
    op.execute(
        """
        DELETE FROM inventory_transfer_template_items AS a
        USING inventory_transfer_template_items AS b
        WHERE a.template_id = b.template_id
          AND a.item_id = b.item_id
          AND a.id > b.id
        """
    )
    op.create_unique_constraint(
        "uq_inventory_transfer_template_item",
        "inventory_transfer_template_items",
        ["template_id", "item_id"],
    )

    # 3. Submitted-document snapshot on the transfer order.
    op.add_column(
        "transfer_orders",
        sa.Column("template_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "transfer_orders",
        sa.Column("template_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "transfer_orders",
        sa.Column("template_snapshot", JSONB(), nullable=True),
    )
    op.create_foreign_key(
        "fk_transfer_orders_template_id",
        "transfer_orders",
        "inventory_transfer_templates",
        ["template_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # Every FK column leads an index (enforced by test_fk_indexes) — the RESTRICT
    # above scans referencing orders when a template is deleted, and the ledger
    # reads transfers by their template.
    op.create_index(
        "ix_transfer_orders_template_id", "transfer_orders", ["template_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_transfer_orders_template_id", table_name="transfer_orders")
    # `IF EXISTS`: on a full `downgrade base`, `230_transfer_parent_fanout` has
    # already round-tripped this table (renaming it to `transfers` and back) and
    # its downgrade restores the `template_id` column and index but not this
    # FK's original name — so by the time we reach here the constraint may be
    # gone. Dropping `template_id` below removes any FK on it regardless; this
    # guard just keeps the explicit drop from crashing the round trip (F-OPS-29).
    op.execute(
        "ALTER TABLE transfer_orders "
        "DROP CONSTRAINT IF EXISTS fk_transfer_orders_template_id"
    )
    op.drop_column("transfer_orders", "template_snapshot")
    op.drop_column("transfer_orders", "template_version")
    op.drop_column("transfer_orders", "template_id")

    op.drop_constraint(
        "uq_inventory_transfer_template_item",
        "inventory_transfer_template_items",
        type_="unique",
    )

    op.drop_constraint(
        "uq_inventory_transfer_template_revision",
        "inventory_transfer_templates",
        type_="unique",
    )
    op.drop_column("inventory_transfer_templates", "version_number")
