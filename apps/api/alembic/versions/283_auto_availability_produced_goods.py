"""Auto off-sale from produced-good stock (experimental, Barsha pilot).

A product or modifier option whose active recipe draws a ``produced_good`` the
branch has run out of (on-hand ≤ 0) is taken off sale automatically, and put
back when the stock recovers. This adds what that needs:

* ``branch_inventory_settings.auto_availability_enabled`` — the per-branch
  switch, off everywhere by default. Turned on for Barsha only, matched by name
  (branch references differ per environment; migration 236's pattern). No
  Barsha branch → no-op, and the flag is set from the admin integrity page.
* On ``branch_products`` and ``branch_modifier_options``, the provenance of an
  off-sale row:
    - ``unavailable_source`` ('staff' | 'auto', NULL while on sale) — String +
      CHECK, per the DB-status convention. Existing off-sale rows are
      backfilled to 'staff': every one of them was put there by a person.
    - ``auto_state`` (jsonb) — the trigger items and their stock snapshot when
      the system took the row off.
    - ``staff_override_until_restock`` — a person put an auto-off row back on
      sale; the system leaves it alone until every trigger item is back > 0.
* Barsha's indefinite stockouts whose recipe already has a produced good at
  ≤ 0 are relabelled 'auto', so they come back on sale by themselves.
* ``inventory_availability_dirty`` — (branch, item) pairs a posting touched,
  upserted inside ``post_transaction`` and drained by the scheduler loop in
  ``app/services/inventory/auto_availability_service.py``.

Additive only: nullable or defaulted columns, one new table. Old POS builds
ignore the new ``unavailable_source`` response field.

Revision ID: 283_auto_availability
Revises: 281_fifo_costing_v3
Create Date: 2026-09-23
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# ≤32 chars: `alembic_version.version_num` is VARCHAR(32), so the file name's
# longer spelling cannot be the id (test_migration_chain enforces it).
revision: str = "283_auto_availability"
# rechain: after 282_counter_promo_branch_modes
down_revision: Union[str, None] = "281_fifo_costing_v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_STOCK_TABLES = ("branch_products", "branch_modifier_options")


def upgrade() -> None:
    op.add_column(
        "branch_inventory_settings",
        sa.Column(
            "auto_availability_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    # The pilot. Guarded to the untouched default so it cannot fight the admin.
    op.execute(
        """
        UPDATE branch_inventory_settings AS s
        SET auto_availability_enabled = true,
            updated_at = now()
        FROM branches AS b
        WHERE s.branch_id = b.id
          AND b.name ILIKE '%barsha%'
          AND b.deleted_at IS NULL
          AND s.auto_availability_enabled = false
        """
    )

    for table in _STOCK_TABLES:
        op.add_column(
            table, sa.Column("unavailable_source", sa.String(10), nullable=True)
        )
        op.add_column(
            table,
            sa.Column(
                "auto_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "staff_override_until_restock",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )
        # Every row off sale today was put there by a person.
        op.execute(
            f"UPDATE {table} SET unavailable_source = 'staff' "
            "WHERE is_in_stock = false AND unavailable_source IS NULL"
        )
        op.create_check_constraint(
            f"ck_{table}_unavailable_source",
            table,
            "unavailable_source IS NULL OR unavailable_source IN ('staff', 'auto')",
        )
        op.create_check_constraint(
            f"ck_{table}_source_only_when_out",
            table,
            "unavailable_source IS NULL OR is_in_stock = false",
        )

    # Barsha's stockouts that are already exactly what the system would do —
    # off indefinitely because a produced good in the active recipe is at ≤ 0 —
    # become system-owned, so they come back by themselves (audited and
    # emailed) when the stock does. A timed stockout stays a person's call.
    # Direct recipe lines only; the full sweep handles anything deeper.
    for table, owner_col, recipe_col, consumes in (
        ("branch_products", "product_id", "product_id", True),
        ("branch_modifier_options", "modifier_option_id", "modifier_option_id", False),
    ):
        consumes_join = (
            f"JOIN products AS p ON p.id = row_.{owner_col} AND p.consumes_stock"
            if consumes
            else ""
        )
        op.execute(
            f"""
            UPDATE {table} AS t
            SET unavailable_source = 'auto',
                auto_state = jsonb_build_object(
                    'items', d.items, 'at', to_jsonb(now()), 'relabelled', true
                )
            FROM (
                SELECT row_.id,
                       jsonb_agg(jsonb_build_object(
                           'item_id', i.id::text,
                           'name', i.name,
                           'on_hand', COALESCE(lv.q, 0)::text
                       )) AS items
                FROM {table} AS row_
                JOIN branch_inventory_settings AS s
                  ON s.branch_id = row_.branch_id AND s.auto_availability_enabled
                {consumes_join}
                JOIN recipes AS r ON r.{recipe_col} = row_.{owner_col}
                JOIN recipe_versions AS rv
                  ON rv.recipe_id = r.id AND rv.status = 'active'
                JOIN recipe_lines AS rl ON rl.recipe_version_id = rv.id
                JOIN inventory_items AS i
                  ON i.id = rl.item_id AND i.kind = 'produced_good'
                LEFT JOIN LATERAL (
                    SELECT SUM(l.quantity) AS q
                    FROM inventory_levels AS l
                    JOIN warehouses AS w ON w.id = l.warehouse_id
                    WHERE w.branch_id = row_.branch_id AND l.item_id = i.id
                ) AS lv ON true
                WHERE row_.is_in_stock = false
                  AND row_.out_of_stock_until IS NULL
                  AND row_.unavailable_source = 'staff'
                  AND COALESCE(lv.q, 0) <= 0
                GROUP BY row_.id
            ) AS d
            WHERE t.id = d.id
            """
        )

    op.create_table(
        "inventory_availability_dirty",
        sa.Column(
            "branch_id",
            sa.UUID(),
            sa.ForeignKey("branches.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "item_id",
            sa.UUID(),
            sa.ForeignKey("inventory_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        # Informational (which posting marked it). No foreign key: the ledger
        # is immutable so it would never cascade, and the posting path should
        # not pay a key-share lock on the transaction row for a breadcrumb.
        sa.Column("last_txn_id", sa.UUID(), nullable=True),
        # clock_timestamp, not now(): the drain deletes a row only if it still
        # carries the stamp it read, and two marks inside one transaction must
        # not share a value.
        sa.Column(
            "marked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("inventory_availability_dirty")
    for table in _STOCK_TABLES:
        op.drop_constraint(f"ck_{table}_source_only_when_out", table, type_="check")
        op.drop_constraint(f"ck_{table}_unavailable_source", table, type_="check")
        op.drop_column(table, "staff_override_until_restock")
        op.drop_column(table, "auto_state")
        op.drop_column(table, "unavailable_source")
    op.drop_column("branch_inventory_settings", "auto_availability_enabled")
