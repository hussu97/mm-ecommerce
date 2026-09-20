"""Create the unified customer-directory cache and normalise source phones.

The customer list is derived from accounts and the canonical MM order ledger.
The latter already receives website, counter and promoted marketplace orders;
using it prevents a marketplace mirror and its promoted order being counted
twice. Statement triggers make the cache pull-through: the next customer read
rebuilds it atomically after any source change.

Revision ID: 272_customer_directory_cache
Revises: 271_lotus_grubops_brand_scope
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import Sequence, Union

import phonenumbers
import sqlalchemy as sa

from alembic import op

revision: str = "272_customer_directory_cache"
down_revision: Union[str, None] = "271_lotus_grubops_brand_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _describe(raw: str | None) -> tuple[str | None, str | None]:
    if not raw:
        return None, None
    for candidate in (raw, "".join(c for c in raw if c.isdigit() or c == "+")):
        try:
            number = phonenumbers.parse(candidate, "AE")
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(number):
            return (
                phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
                phonenumbers.region_code_for_number(number),
            )
    return None, None


def _normalise_column(table: str, country_column: str) -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            f"SELECT id, customer_phone FROM {table} WHERE customer_phone IS NOT NULL"
        )
    )
    statement = sa.text(
        f"UPDATE {table} SET customer_phone = :phone, {country_column} = :country "
        "WHERE id = :id"
    )
    for row_id, raw in rows:
        phone, country = _describe(raw)
        if phone:
            conn.execute(statement, {"id": row_id, "phone": phone, "country": country})


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("phone_country", sa.String(length=2), nullable=True)
    )
    op.add_column(
        "aggregator_order",
        sa.Column("customer_phone_country", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "custom_orders",
        sa.Column("customer_phone_country", sa.String(length=2), nullable=True),
    )

    conn = op.get_bind()
    user_rows = conn.execute(
        sa.text("SELECT id, phone FROM users WHERE phone IS NOT NULL")
    )
    for row_id, raw in user_rows:
        phone, country = _describe(raw)
        if phone:
            conn.execute(
                sa.text(
                    "UPDATE users SET phone = :phone, phone_country = :country WHERE id = :id"
                ),
                {"id": row_id, "phone": phone, "country": country},
            )
    _normalise_column("aggregator_order", "customer_phone_country")
    _normalise_column("custom_orders", "customer_phone_country")

    op.create_table(
        "customer_cache_state",
        sa.Column("id", sa.Boolean(), primary_key=True, server_default=sa.text("true")),
        sa.Column(
            "dirty", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.CheckConstraint("id IS TRUE", name="ck_customer_cache_state_singleton"),
    )
    op.execute(
        sa.text("INSERT INTO customer_cache_state (id, dirty) VALUES (true, true)")
    )
    op.create_table(
        "customer_cache",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("name", sa.String(length=150), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=30), nullable=True),
        sa.Column("phone_country", sa.String(length=2), nullable=True),
        sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("earliest_order_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_order_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "total_revenue", sa.Numeric(12, 2), nullable=False, server_default="0"
        ),
        sa.Column("aov", sa.Numeric(12, 2), nullable=False, server_default="0"),
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
    )
    op.create_index("ix_customer_cache_name", "customer_cache", ["name"])
    op.create_index("ix_customer_cache_email", "customer_cache", ["email"])
    op.create_index("ix_customer_cache_phone", "customer_cache", ["phone"])
    op.create_table(
        "customer_order_cache",
        sa.Column(
            "customer_id",
            sa.UUID(),
            sa.ForeignKey("customer_cache.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "order_id",
            sa.UUID(),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            primary_key=True,
            unique=True,
        ),
    )
    op.execute(
        """
        CREATE FUNCTION mark_customer_cache_dirty() RETURNS trigger AS $$
        BEGIN
            UPDATE customer_cache_state SET dirty = true WHERE id IS TRUE;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("orders", "users"):
        op.execute(
            sa.text(
                f"CREATE TRIGGER {table}_customer_cache_dirty "
                f"AFTER INSERT OR UPDATE OR DELETE ON {table} "
                "FOR EACH STATEMENT EXECUTE FUNCTION mark_customer_cache_dirty()"
            )
        )


def downgrade() -> None:
    for table in ("orders", "users"):
        op.execute(sa.text(f"DROP TRIGGER {table}_customer_cache_dirty ON {table}"))
    op.execute("DROP FUNCTION mark_customer_cache_dirty()")
    op.drop_table("customer_order_cache")
    op.drop_index("ix_customer_cache_phone", table_name="customer_cache")
    op.drop_index("ix_customer_cache_email", table_name="customer_cache")
    op.drop_index("ix_customer_cache_name", table_name="customer_cache")
    op.drop_table("customer_cache")
    op.drop_table("customer_cache_state")
    op.drop_column("custom_orders", "customer_phone_country")
    op.drop_column("aggregator_order", "customer_phone_country")
    op.drop_column("users", "phone_country")
