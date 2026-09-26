"""PO misc-item categories, period presets, and a category + period per misc line.

A misc (non-stock) purchase-order line now says **what** the spend is (a
category shared across suppliers) and **which days it covers** (a from–to date
range), so the P&L can spread a cost like rent across its period instead of
booking it on the PO date.

- ``purchase_order_misc_categories``: the category list. ``admin_only`` marks a
  confidential category (rent, salary, …) that the till never sees and admin
  shows only to holders of ``inventory.purchase_orders.restricted_misc``.
- ``purchase_order_misc_periods``: the preset list the pickers offer ("This
  month" …). A preset is a ``unit`` (day/week/month) and a ``length``; it only
  pre-fills the range — lines store the dates, never the preset.
- ``purchase_order_misc_items`` gains ``category_id``, ``period_from`` and
  ``period_to``, all required. The four lines that existed at cut-over are
  backfilled by id (owner-chosen, 2026-09-26).

Everything is literal SQL: asyncpg types bound parameters strictly, and the
UUID/timestamp defaults of the ORM mixins are Python-side only.

Revision ID: 293_po_misc_categories
Revises: 292_production_restatement
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "293_po_misc_categories"
down_revision: Union[str, None] = "292_production_restatement"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: (name, admin_only)
_CATEGORIES = (
    ("Cake Supplies", False),
    ("Groceries", False),
    ("Rent", True),
    ("Utilities", False),
    ("Cleaning Supplies", False),
    ("Salary", True),
    ("Trade License", True),
    ("Misc. Government Expense", False),
    ("Health Insurance and Visa", True),
)

#: (name, unit, length, is_default, display_order)
_PERIODS = (
    ("Today", "day", 1, False, 1),
    ("This week", "week", 1, False, 2),
    ("This month", "month", 1, True, 3),
    ("This quarter", "month", 3, False, 4),
    ("This year", "month", 12, False, 5),
)

#: The misc lines on prod at cut-over → their category. Period is Sept 2026.
_BACKFILL = (
    ("8b598bf4-5dcf-4a2e-b305-8c0ec83f2040", "Cake Supplies"),
    ("e23db1c1-9491-43bc-903a-7da55be6a49e", "Cake Supplies"),
    ("c7e53f24-46cc-4dd1-88c0-6ebee27edd55", "Cake Supplies"),
    ("54ef9a8d-2fe8-48fe-ab49-e3e3982f11ce", "Groceries"),
)
_BACKFILL_FROM = "2026-09-01"
_BACKFILL_TO = "2026-09-30"
#: Any other line (a dev/staging database) falls back to this category.
_FALLBACK_CATEGORY = "Cake Supplies"


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "purchase_order_misc_categories",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "admin_only", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "uq_purchase_order_misc_categories_name",
        "purchase_order_misc_categories",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "purchase_order_misc_periods",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("unit", sa.String(length=10), nullable=False),
        sa.Column("length", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            "unit IN ('day', 'week', 'month')",
            name="ck_purchase_order_misc_periods_unit",
        ),
        sa.CheckConstraint("length >= 1", name="ck_purchase_order_misc_periods_length"),
    )
    op.create_index(
        "uq_purchase_order_misc_periods_name",
        "purchase_order_misc_periods",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    # At most one live default preset.
    op.create_index(
        "uq_purchase_order_misc_periods_default",
        "purchase_order_misc_periods",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default AND deleted_at IS NULL"),
    )

    categories = ", ".join(
        f"({_quote(name)}, {'true' if admin_only else 'false'})"
        for name, admin_only in _CATEGORIES
    )
    op.execute(
        "INSERT INTO purchase_order_misc_categories (name, admin_only) "
        f"VALUES {categories}"
    )
    periods = ", ".join(
        f"({_quote(name)}, {_quote(unit)}, {length}, "
        f"{'true' if default else 'false'}, {order})"
        for name, unit, length, default, order in _PERIODS
    )
    op.execute(
        "INSERT INTO purchase_order_misc_periods "
        f"(name, unit, length, is_default, display_order) VALUES {periods}"
    )

    op.add_column(
        "purchase_order_misc_items",
        sa.Column(
            "category_id",
            sa.UUID(),
            sa.ForeignKey("purchase_order_misc_categories.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_purchase_order_misc_items_category_id",
        "purchase_order_misc_items",
        ["category_id"],
    )
    op.add_column(
        "purchase_order_misc_items",
        sa.Column("period_from", sa.Date(), nullable=True),
    )
    op.add_column(
        "purchase_order_misc_items",
        sa.Column("period_to", sa.Date(), nullable=True),
    )

    for line_id, category in _BACKFILL:
        op.execute(
            "UPDATE purchase_order_misc_items SET "
            "category_id = (SELECT id FROM purchase_order_misc_categories "
            f"WHERE name = {_quote(category)}), "
            f"period_from = DATE {_quote(_BACKFILL_FROM)}, "
            f"period_to = DATE {_quote(_BACKFILL_TO)} "
            f"WHERE id = {_quote(line_id)}::uuid"
        )
    # Any line not named above (non-prod databases): its PO's calendar month.
    op.execute(
        "UPDATE purchase_order_misc_items m SET "
        "category_id = (SELECT id FROM purchase_order_misc_categories "
        f"WHERE name = {_quote(_FALLBACK_CATEGORY)}), "
        "period_from = date_trunc('month', po.business_date::date)::date, "
        "period_to = (date_trunc('month', po.business_date::date) "
        "+ interval '1 month - 1 day')::date "
        "FROM purchase_orders po "
        "WHERE po.id = m.purchase_order_id AND m.category_id IS NULL"
    )
    for column in ("category_id", "period_from", "period_to"):
        op.alter_column("purchase_order_misc_items", column, nullable=False)
    op.create_check_constraint(
        "ck_purchase_order_misc_items_period",
        "purchase_order_misc_items",
        "period_to >= period_from",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_purchase_order_misc_items_period", "purchase_order_misc_items"
    )
    op.drop_index(
        "ix_purchase_order_misc_items_category_id",
        table_name="purchase_order_misc_items",
    )
    op.drop_column("purchase_order_misc_items", "period_to")
    op.drop_column("purchase_order_misc_items", "period_from")
    op.drop_column("purchase_order_misc_items", "category_id")
    op.drop_table("purchase_order_misc_periods")
    op.drop_table("purchase_order_misc_categories")
