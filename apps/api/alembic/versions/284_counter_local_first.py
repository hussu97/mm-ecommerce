"""Local-first counter checkout: bundles, synced-sale bookkeeping, ticket prefixes.

A counter sale can now be rung up, priced, paid, numbered and printed on the
iPad and synced afterwards (`POST /pos/counter/sales`). The device prices from a
server-published, content-addressed config bundle (`GET /pos/counter/bundle`)
and the server re-prices every synced sale against the exact bundle it cites.

Additive only — every existing POS endpoint keeps its contract, and a terminal
on an older build never touches any of this:

* `orders`: `display_number` (the printed `T1-0042`), `order_number` widened
  30 → 40 (`POS-{ref[:10]}-{date}-{display_number}`), `pricing_status`
  (`verified|mismatch|unverified`, String + CHECK), `pricing_audit`,
  `config_bundle_hash`, `priced_at`, `ingested_at`, `ingest_payload_sha`,
  `ingested_late`, `ingest_flags`, and a partial unique index on
  `(branch_id, business_date, display_number)`.
* `devices`: `ticket_prefix` (unique per branch, partial), `counter_mode`
  (`online|shadow|local`, CHECK), and the heartbeat's unsynced-sale counts
  `pending_sales`, `parked_sales`, `oldest_pending_sale_at`, `sync_reported_at`.
* `kitchen_tickets.origin` (`server|device`, CHECK, default `server`).
  `printed_at` already exists.
* `tills.totals_restated_at`.
* `branches.counter_local_first` (`off|shadow|on`, CHECK, default `off`) — the
  per-branch rollout flag and kill switch.
* New tables `pos_config_bundles` and `counter_sale_quarantine`.

Widening a varchar and adding a column with a constant default are both
metadata-only in Postgres 11+, so this does not rewrite `orders`.

Revision ID: 284_counter_local_first
Revises: 283_auto_availability
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "284_counter_local_first"
down_revision: Union[str, None] = "283_auto_availability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: The Counter sync console's "needs a look" predicate.
_ATTENTION = (
    "cardinality(ingest_flags) > 0 OR ingested_late "
    "OR (ingested_at IS NOT NULL AND pricing_status IS DISTINCT FROM 'verified')"
)


def upgrade() -> None:
    # ─── orders ──────────────────────────────────────────────────────────────
    op.alter_column(
        "orders",
        "order_number",
        existing_type=sa.String(30),
        type_=sa.String(40),
        existing_nullable=False,
    )
    op.add_column("orders", sa.Column("display_number", sa.String(20), nullable=True))
    op.add_column("orders", sa.Column("pricing_status", sa.String(20), nullable=True))
    op.add_column(
        "orders",
        sa.Column("pricing_audit", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "orders", sa.Column("config_bundle_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "orders", sa.Column("priced_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "orders", sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "orders", sa.Column("ingest_payload_sha", sa.String(64), nullable=True)
    )
    op.add_column(
        "orders",
        sa.Column(
            "ingested_late",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "orders",
        sa.Column(
            "ingest_flags",
            postgresql.ARRAY(sa.String()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.create_check_constraint(
        "ck_orders_pricing_status_allowed",
        "orders",
        "pricing_status IS NULL OR pricing_status IN "
        "('verified', 'mismatch', 'unverified')",
    )
    # `orders` is hot: a plain CREATE INDEX would block every sale for the
    # build, so both go in CONCURRENTLY outside the migration's transaction
    # (migration 210's pattern). The columns above commit first.
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_orders_branch_business_date_display_number",
            "orders",
            ["branch_id", "business_date", "display_number"],
            unique=True,
            postgresql_where=sa.text("display_number IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        # The Counter sync console reads "every counter sale that needs a
        # look"; partial so it indexes only the handful that do. Mirrors the
        # console's WHERE clause (`pos_counter.counter_sync_overview`) exactly.
        op.create_index(
            "ix_orders_counter_sync_attention",
            "orders",
            ["ingested_at"],
            postgresql_where=sa.text(_ATTENTION),
            postgresql_concurrently=True,
            if_not_exists=True,
        )

    # ─── devices ─────────────────────────────────────────────────────────────
    op.add_column("devices", sa.Column("ticket_prefix", sa.String(6), nullable=True))
    op.add_column("devices", sa.Column("counter_mode", sa.String(10), nullable=True))
    op.add_column("devices", sa.Column("pending_sales", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("parked_sales", sa.Integer(), nullable=True))
    op.add_column(
        "devices",
        sa.Column("oldest_pending_sale_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "devices",
        sa.Column("sync_reported_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_devices_branch_ticket_prefix",
        "devices",
        ["branch_id", "ticket_prefix"],
        unique=True,
        postgresql_where=sa.text("ticket_prefix IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_devices_counter_mode_allowed",
        "devices",
        "counter_mode IS NULL OR counter_mode IN ('online', 'shadow', 'local')",
    )

    # ─── kitchen_tickets / tills / branches ──────────────────────────────────
    op.add_column(
        "kitchen_tickets",
        sa.Column(
            "origin",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'server'"),
        ),
    )
    op.create_check_constraint(
        "ck_kitchen_tickets_origin_allowed",
        "kitchen_tickets",
        "origin IN ('server', 'device')",
    )
    op.add_column(
        "tills",
        sa.Column("totals_restated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "branches",
        sa.Column(
            "counter_local_first",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'off'"),
        ),
    )
    op.create_check_constraint(
        "ck_branches_counter_local_first_allowed",
        "branches",
        "counter_local_first IN ('off', 'shadow', 'on')",
    )

    # ─── pos_config_bundles ──────────────────────────────────────────────────
    op.create_table(
        "pos_config_bundles",
        sa.Column("hash", sa.String(64), primary_key=True),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("engine_version", sa.Integer(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_served_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_pos_config_bundles_branch_id", "pos_config_bundles", ["branch_id"]
    )
    op.create_index(
        "ix_pos_config_bundles_last_served_at",
        "pos_config_bundles",
        ["last_served_at"],
    )

    # ─── counter_sale_quarantine ─────────────────────────────────────────────
    op.create_table(
        "counter_sale_quarantine",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "branch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha", sa.String(64), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "resolved_by_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
    )
    for column in ("device_id", "branch_id", "resolved_by_id"):
        op.create_index(
            f"ix_counter_sale_quarantine_{column}",
            "counter_sale_quarantine",
            [column],
        )


def downgrade() -> None:
    # Refuse before touching anything: narrowing `order_number` back to 30
    # would truncate a local-first invoice number, and the concurrent index
    # drops below commit on their own — failing after them would strand a
    # half-downgraded schema still stamped 284.
    long_number = (
        op.get_bind()
        .execute(
            sa.text("SELECT 1 FROM orders WHERE length(order_number) > 30 LIMIT 1")
        )
        .first()
    )
    if long_number is not None:
        raise RuntimeError(
            "284 downgrade refused: orders carry local-first order numbers "
            "longer than 30 characters"
        )

    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_orders_counter_sync_attention",
            table_name="orders",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "uq_orders_branch_business_date_display_number",
            table_name="orders",
            postgresql_concurrently=True,
            if_exists=True,
        )

    op.drop_table("counter_sale_quarantine")
    op.drop_table("pos_config_bundles")

    op.drop_constraint(
        "ck_branches_counter_local_first_allowed", "branches", type_="check"
    )
    op.drop_column("branches", "counter_local_first")
    op.drop_column("tills", "totals_restated_at")
    op.drop_constraint(
        "ck_kitchen_tickets_origin_allowed", "kitchen_tickets", type_="check"
    )
    op.drop_column("kitchen_tickets", "origin")

    op.drop_constraint("ck_devices_counter_mode_allowed", "devices", type_="check")
    op.drop_index("uq_devices_branch_ticket_prefix", table_name="devices")
    for column in (
        "sync_reported_at",
        "oldest_pending_sale_at",
        "parked_sales",
        "pending_sales",
        "counter_mode",
        "ticket_prefix",
    ):
        op.drop_column("devices", column)

    op.drop_constraint("ck_orders_pricing_status_allowed", "orders", type_="check")
    for column in (
        "ingest_flags",
        "ingested_late",
        "ingest_payload_sha",
        "ingested_at",
        "priced_at",
        "config_bundle_hash",
        "pricing_audit",
        "pricing_status",
        "display_number",
    ):
        op.drop_column("orders", column)
    # Narrowing back fails loudly if a local-first order number (up to 40
    # characters) is still present — the right outcome for a downgrade that
    # would otherwise truncate an invoice number.
    op.alter_column(
        "orders",
        "order_number",
        existing_type=sa.String(40),
        type_=sa.String(30),
        existing_nullable=False,
    )
