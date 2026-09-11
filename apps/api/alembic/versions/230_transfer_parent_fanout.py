"""Transfers become a parent order → per-branch children fan-out.

The per-branch transfer object (``transfer_orders`` / ``TransferOrder``) is
renamed to the *child* ``transfers`` / ``Transfer``, and a new *parent*
``transfer_orders`` / ``TransferOrder`` is created: one admin action from a
single source branch, fanning out one child per destination branch.

Steps:

1. Rename ``transfer_orders`` → ``transfers`` and ``transfer_order_items`` →
   ``transfer_items`` (column ``transfer_order_id`` → ``transfer_id``). Every
   index is moved off the ``ix_transfer_orders_*`` name-space because index
   names are schema-global and the new parent reuses them.
2. Migrate the child status vocab draft/pending/accepted/declined/closed →
   pending/sent/closed/cancelled, derived from the two link columns.
3. Create the parent ``transfer_orders``.
4/5. Add the child→parent link and backfill one parent per existing child
   (there is one child per branch today), inheriting its order-level fields.
6. Drop the columns that moved to the parent (template provenance, the request
   /accept actor columns, ``required_date``, ``client_request_id``).
7. Re-point ledger ``source_type`` ``transfer_order`` → ``transfer``.
8. Grant the new ``inventory.transfers.send``/``.receive`` slugs to any role
   already holding ``inventory.transfers.manage``.

Revision ID: 230_transfer_parent_fanout
Revises: 229_drop_pos_tags
Create Date: 2026-09-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

revision: str = "230_transfer_parent_fanout"
down_revision: Union[str, None] = "229_drop_pos_tags"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHILD_STATUS = "('pending', 'sent', 'closed', 'cancelled')"
PARENT_STATUS = (
    "('pending', 'partially_sent', 'sent', 'partially_received', 'closed', 'cancelled')"
)

# Indexes and the client_request_id partial-unique index on the old
# transfer_orders table, moved onto the child's `transfers` name-space so the
# freshly-created parent can reuse the `_transfer_orders_` names.
_CHILD_INDEX_RENAMES = [
    ("ix_transfer_orders_reference", "ix_transfers_reference"),
    ("ix_transfer_orders_status", "ix_transfers_status"),
    ("ix_transfer_orders_branch_id", "ix_transfers_branch_id"),
    ("ix_transfer_orders_source_branch_id", "ix_transfers_source_branch_id"),
    ("ix_transfer_orders_business_date", "ix_transfers_business_date"),
    ("ix_transfer_orders_kind", "ix_transfers_kind"),
    ("uq_transfer_orders_client_request_id", "uq_transfers_client_request_id"),
    ("ix_transfer_orders_template_id", "ix_transfers_template_id"),
    ("ix_transfer_order_items_transfer_order_id", "ix_transfer_items_transfer_id"),
    ("ix_transfer_order_items_item_id", "ix_transfer_items_item_id"),
]

# Columns that move from the (now child) table onto the new parent.
_MOVED_TO_PARENT = [
    "client_request_id",
    "submitter_id",
    "responder_id",
    "submitted_at",
    "responded_at",
    "required_date",
    "template_id",
    "template_version",
    "template_snapshot",
]


def upgrade() -> None:
    # ── 1. Rename the per-branch object to the child `transfers`. ──
    op.rename_table("transfer_orders", "transfers")
    op.rename_table("transfer_order_items", "transfer_items")
    op.alter_column(
        "transfer_items", "transfer_order_id", new_column_name="transfer_id"
    )
    for old, new in _CHILD_INDEX_RENAMES:
        op.execute(f"ALTER INDEX {old} RENAME TO {new}")
    op.execute(
        "ALTER TABLE transfers RENAME CONSTRAINT "
        "ck_transfer_orders_kind TO ck_transfers_kind"
    )
    op.execute(
        "ALTER TABLE transfers RENAME CONSTRAINT "
        "ck_transfer_orders_business_date_format TO "
        "ck_transfers_business_date_format"
    )

    # ── 2. Child status vocabulary, derived from the two link columns. Drop the
    #    old CHECK first — the new values (sent/cancelled) would violate it — then
    #    migrate the data, then add the new CHECK. ──
    op.drop_constraint("ck_transfer_orders_status_allowed", "transfers", type_="check")
    op.execute(
        """
        UPDATE transfers SET status = CASE
            WHEN received_transaction_id IS NOT NULL THEN 'closed'
            WHEN sent_transaction_id IS NOT NULL THEN 'sent'
            WHEN status = 'declined' THEN 'cancelled'
            ELSE 'pending'
        END
        """
    )
    op.create_check_constraint(
        "ck_transfers_status_allowed", "transfers", f"status IN {CHILD_STATUS}"
    )
    op.alter_column(
        "transfers", "status", existing_type=sa.String(20), server_default="pending"
    )

    # ── 3. New parent `transfer_orders`. ──
    op.create_table(
        "transfer_orders",
        sa.Column("id", UUID(as_uuid=True), nullable=False),
        sa.Column("reference", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("kind", sa.String(20), nullable=False, server_default="transfer"),
        sa.Column("source_branch_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_warehouse_id", UUID(as_uuid=True), nullable=True),
        sa.Column("business_date", sa.String(10), nullable=False),
        sa.Column("required_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.String(64), nullable=True),
        sa.Column("adjustment_group_id", UUID(as_uuid=True), nullable=True),
        sa.Column("creator_id", UUID(as_uuid=True), nullable=True),
        sa.Column("template_id", UUID(as_uuid=True), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("template_snapshot", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_branch_id"], ["branches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_warehouse_id"], ["warehouses.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["creator_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["inventory_transfer_templates.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            f"status IN {PARENT_STATUS}", name="ck_transfer_orders_status_allowed"
        ),
        sa.CheckConstraint(
            r"business_date ~ '^\d{4}-\d{2}-\d{2}$'",
            name="ck_transfer_orders_business_date_format",
        ),
        sa.CheckConstraint(
            "kind IN ('transfer', 'return')", name="ck_transfer_orders_kind"
        ),
    )
    op.create_index(
        "ix_transfer_orders_reference", "transfer_orders", ["reference"], unique=True
    )
    op.create_index("ix_transfer_orders_status", "transfer_orders", ["status"])
    op.create_index("ix_transfer_orders_kind", "transfer_orders", ["kind"])
    op.create_index(
        "ix_transfer_orders_source_branch_id", "transfer_orders", ["source_branch_id"]
    )
    op.create_index(
        "ix_transfer_orders_business_date", "transfer_orders", ["business_date"]
    )
    op.create_index(
        "ix_transfer_orders_template_id", "transfer_orders", ["template_id"]
    )
    op.create_index(
        "ix_transfer_orders_adjustment_group_id",
        "transfer_orders",
        ["adjustment_group_id"],
    )
    op.create_index(
        "uq_transfer_orders_client_request_id",
        "transfer_orders",
        ["client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )

    # ── 4. Child → parent link, nullable for the backfill. ──
    op.add_column(
        "transfers", sa.Column("transfer_order_id", UUID(as_uuid=True), nullable=True)
    )

    # ── 5. One parent per existing child. The parent reuses the child's
    #    reference (unique per table, so the join is 1:1 and human-readable);
    #    only these migrated rows share a reference across the two tables —
    #    new children mint `TRF-` while new parents mint `TO-`. Parent status
    #    is the single child's status rolled up. ──
    op.execute(
        """
        INSERT INTO transfer_orders (
            id, reference, status, kind, source_branch_id, source_warehouse_id,
            business_date, required_date, notes, client_request_id,
            creator_id, template_id, template_version, template_snapshot,
            created_at, updated_at
        )
        SELECT
            gen_random_uuid(), t.reference,
            CASE
                WHEN t.status = 'closed' THEN 'closed'
                WHEN t.status = 'sent' THEN 'sent'
                WHEN t.status = 'cancelled' THEN 'cancelled'
                ELSE 'pending'
            END,
            t.kind, t.source_branch_id, t.source_warehouse_id,
            t.business_date, t.required_date, t.notes, t.client_request_id,
            t.creator_id, t.template_id, t.template_version, t.template_snapshot,
            t.created_at, t.updated_at
        FROM transfers t
        """
    )
    op.execute(
        "UPDATE transfers t SET transfer_order_id = o.id "
        "FROM transfer_orders o WHERE o.reference = t.reference"
    )
    op.alter_column("transfers", "transfer_order_id", nullable=False)
    op.create_foreign_key(
        "fk_transfers_transfer_order_id",
        "transfers",
        "transfer_orders",
        ["transfer_order_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_transfers_transfer_order_id", "transfers", ["transfer_order_id"]
    )

    # ── 6. Drop the columns that now live on the parent. Dropping a column
    #    takes its dependent index/FK with it (client_request_id's unique index,
    #    template_id's FK + index, the submitter/responder FKs). ──
    for column in _MOVED_TO_PARENT:
        op.drop_column("transfers", column)

    # ── 7. One ledger code path: transfer legs point at source_type 'transfer'
    #    (source_id is the child id, unchanged by the rename). ──
    op.execute(
        "UPDATE inventory_transactions SET source_type = 'transfer' "
        "WHERE source_type = 'transfer_order'"
    )

    # ── 8. A role that could manage transfers keeps the send + receive rights
    #    the old single slug implied. ──
    op.execute(
        """
        UPDATE roles
        SET permissions = (
            SELECT array_agg(DISTINCT p) FROM unnest(
                permissions
                || ARRAY['inventory.transfers.send', 'inventory.transfers.receive']
            ) AS p
        )
        WHERE 'inventory.transfers.manage' = ANY(permissions)
        """
    )


def downgrade() -> None:
    # Reverse the permission grant (best-effort; a role that always had send is
    # indistinguishable from one that gained it here, which is acceptable).
    op.execute(
        """
        UPDATE roles SET permissions = (
            SELECT COALESCE(array_agg(p), ARRAY[]::varchar[])
            FROM unnest(permissions) AS p
            WHERE p NOT IN (
                'inventory.transfers.send', 'inventory.transfers.receive'
            )
        )
        """
    )
    op.execute(
        "UPDATE inventory_transactions SET source_type = 'transfer_order' "
        "WHERE source_type = 'transfer'"
    )

    # Restore the moved columns on the child, copying template provenance and
    # required_date back from the parent before the parent is dropped. The
    # request/accept actor columns cannot be reconstructed and come back null.
    op.add_column("transfers", sa.Column("required_date", sa.Date(), nullable=True))
    op.add_column(
        "transfers", sa.Column("client_request_id", sa.String(64), nullable=True)
    )
    op.add_column(
        "transfers", sa.Column("submitter_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "transfers", sa.Column("responder_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "transfers",
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transfers",
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "transfers", sa.Column("template_id", UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "transfers", sa.Column("template_version", sa.Integer(), nullable=True)
    )
    op.add_column("transfers", sa.Column("template_snapshot", JSONB(), nullable=True))
    op.execute(
        """
        UPDATE transfers t SET
            required_date = o.required_date,
            client_request_id = o.client_request_id,
            template_id = o.template_id,
            template_version = o.template_version,
            template_snapshot = o.template_snapshot
        FROM transfer_orders o WHERE t.transfer_order_id = o.id
        """
    )
    op.create_foreign_key(
        "fk_transfers_submitter_id",
        "transfers",
        "users",
        ["submitter_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transfers_responder_id",
        "transfers",
        "users",
        ["responder_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_transfers_template_id",
        "transfers",
        "inventory_transfer_templates",
        ["template_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_transfers_template_id", "transfers", ["template_id"])
    op.create_index(
        "uq_transfers_client_request_id",
        "transfers",
        ["client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )

    # Drop the child → parent link and the parent table.
    op.drop_index("ix_transfers_transfer_order_id", table_name="transfers")
    op.drop_constraint(
        "fk_transfers_transfer_order_id", "transfers", type_="foreignkey"
    )
    op.drop_column("transfers", "transfer_order_id")
    op.drop_table("transfer_orders")

    # Restore the old child status vocabulary (sent → accepted, cancelled →
    # declined; closed/pending unchanged).
    op.drop_constraint("ck_transfers_status_allowed", "transfers", type_="check")
    op.execute(
        """
        UPDATE transfers SET status = CASE
            WHEN status = 'sent' THEN 'accepted'
            WHEN status = 'cancelled' THEN 'declined'
            ELSE status
        END
        """
    )
    op.create_check_constraint(
        "ck_transfer_orders_status_allowed",
        "transfers",
        "status IN ('draft', 'pending', 'accepted', 'declined', 'closed')",
    )
    op.alter_column(
        "transfers", "status", existing_type=sa.String(20), server_default="draft"
    )

    # Rename everything back to the transfer_orders name-space.
    op.execute(
        "ALTER TABLE transfers RENAME CONSTRAINT "
        "ck_transfers_kind TO ck_transfer_orders_kind"
    )
    op.execute(
        "ALTER TABLE transfers RENAME CONSTRAINT "
        "ck_transfers_business_date_format TO "
        "ck_transfer_orders_business_date_format"
    )
    for old, new in _CHILD_INDEX_RENAMES:
        op.execute(f"ALTER INDEX {new} RENAME TO {old}")
    op.alter_column(
        "transfer_items", "transfer_id", new_column_name="transfer_order_id"
    )
    op.rename_table("transfer_items", "transfer_order_items")
    op.rename_table("transfers", "transfer_orders")
