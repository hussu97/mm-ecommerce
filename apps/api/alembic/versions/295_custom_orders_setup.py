"""Custom orders v2: settings, invoice fields, and the content the channel needs.

- ``business_settings`` names the custom-orders branch, product and inventory
  category. Seeded by lookup (branch ``K001``, product ``FG0119``) rather than
  hard-coded, so an environment whose references differ simply resolves NULL
  and the channel stays off until an admin sets it.
- The inventory category "Customized Cake Raw Materials" is created.
- FG0119 stops consuming stock through a product recipe: a custom order's
  consumption is its own recipe, so a product recipe on FG0119 would be
  counted twice, and without one every recipe-gap report flags it.
- ``legal_entities`` gains what an A4 invoice needs (registered address, bank
  details, CC list); the owners are copied on Fatema Cake Sweets' invoices.
- ``orders.custom.manage`` reaches every role that already produces, so the
  Sharjah registers can take and pack custom orders.
- The "Card payment fee" charge a custom order bills a card fee as: inactive
  (so no till offers it), taxed like FG0119, found by its reference.

Every content write is guarded to the value it replaces (canon rule 7).
Literal SQL throughout: asyncpg types bound parameters strictly.

Revision ID: 295_custom_orders_setup
Revises: 294_custom_orders_v2
Create Date: 2026-09-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision: str = "295_custom_orders_setup"
down_revision: Union[str, None] = "294_custom_orders_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CATEGORY_REFERENCE = "customized-cake-raw-materials"
_CARD_FEE_CHARGE_REFERENCE = "custom-order-card-fee"


def upgrade() -> None:
    # ── Settings ─────────────────────────────────────────────────────────────
    op.add_column(
        "business_settings",
        sa.Column(
            "custom_orders_branch_id",
            UUID(as_uuid=True),
            sa.ForeignKey("branches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "business_settings",
        sa.Column(
            "custom_orders_product_id",
            UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "business_settings",
        sa.Column(
            "custom_orders_inventory_category_id",
            UUID(as_uuid=True),
            sa.ForeignKey("inventory_categories.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    op.execute(
        f"""
        INSERT INTO inventory_categories
            (id, name, reference, display_order, is_active, translations,
             created_at, updated_at)
        SELECT gen_random_uuid(), 'Customized Cake Raw Materials',
               '{_CATEGORY_REFERENCE}', 15, true, '{{}}'::jsonb, now(), now()
        WHERE NOT EXISTS (
            SELECT 1 FROM inventory_categories
            WHERE reference = '{_CATEGORY_REFERENCE}'
        )
        """
    )
    op.execute(
        f"""
        UPDATE business_settings SET
            custom_orders_branch_id = COALESCE(
                custom_orders_branch_id,
                (SELECT id FROM branches
                 WHERE reference = 'K001' AND deleted_at IS NULL LIMIT 1)),
            custom_orders_product_id = COALESCE(
                custom_orders_product_id,
                (SELECT id FROM products WHERE sku = 'FG0119' LIMIT 1)),
            custom_orders_inventory_category_id = COALESCE(
                custom_orders_inventory_category_id,
                (SELECT id FROM inventory_categories
                 WHERE reference = '{_CATEGORY_REFERENCE}' LIMIT 1))
        """
    )

    # ── FG0119: consumption comes from each order's own recipe ───────────────
    op.execute(
        "UPDATE products SET consumes_stock = false "
        "WHERE sku = 'FG0119' AND consumes_stock = true"
    )

    # ── Legal entity: what an A4 invoice needs ──────────────────────────────
    op.add_column(
        "legal_entities", sa.Column("registered_address", sa.Text(), nullable=True)
    )
    op.add_column(
        "legal_entities", sa.Column("bank_name", sa.String(120), nullable=True)
    )
    op.add_column(
        "legal_entities", sa.Column("bank_account_name", sa.String(200), nullable=True)
    )
    op.add_column(
        "legal_entities",
        sa.Column("bank_account_number", sa.String(50), nullable=True),
    )
    op.add_column("legal_entities", sa.Column("iban", sa.String(34), nullable=True))
    op.add_column(
        "legal_entities", sa.Column("swift_code", sa.String(11), nullable=True)
    )
    op.add_column(
        "legal_entities",
        sa.Column("invoice_cc_emails", ARRAY(sa.String(255)), nullable=True),
    )
    op.execute(
        """
        UPDATE legal_entities
        SET invoice_cc_emails =
            ARRAY['fatema_f@hotmail.co.uk', 'fahimakhtarabbasi@gmail.com']::varchar[]
        WHERE reference = 'fatema' AND invoice_cc_emails IS NULL
        """
    )

    # ── The card-fee charge ─────────────────────────────────────────────────
    op.execute(
        f"""
        INSERT INTO charges
            (id, name, reference, type, value, is_auto_applied, order_types,
             tax_group_id, is_active, translations, created_at, updated_at)
        SELECT gen_random_uuid(), 'Card payment fee', '{_CARD_FEE_CHARGE_REFERENCE}',
               'fixed', 0, false, '{{}}'::varchar[],
               (SELECT tax_group_id FROM products WHERE sku = 'FG0119' LIMIT 1),
               false, '{{}}'::jsonb, now(), now()
        WHERE NOT EXISTS (
            SELECT 1 FROM charges WHERE reference = '{_CARD_FEE_CHARGE_REFERENCE}'
        )
        """
    )

    # ── Permission: the tills that produce may take custom orders ───────────
    op.execute(
        """
        UPDATE roles
        SET permissions = array_append(permissions, 'orders.custom.manage')
        WHERE permissions @> ARRAY['inventory.production.manage']::varchar[]
          AND NOT (permissions @> ARRAY['orders.custom.manage']::varchar[])
        """
    )


def downgrade() -> None:
    # The `orders.custom.manage` grant stays: the slug predates this migration,
    # and a role that gained it here cannot be told from one that already held
    # it, so removing it would guess.
    for column in (
        "invoice_cc_emails",
        "swift_code",
        "iban",
        "bank_account_number",
        "bank_account_name",
        "bank_name",
        "registered_address",
    ):
        op.drop_column("legal_entities", column)
    op.execute(
        "UPDATE products SET consumes_stock = true "
        "WHERE sku = 'FG0119' AND consumes_stock = false"
    )
    op.execute(
        f"""
        DELETE FROM charges ch
        WHERE ch.reference = '{_CARD_FEE_CHARGE_REFERENCE}'
          AND NOT EXISTS (SELECT 1 FROM order_charges oc WHERE oc.charge_id = ch.id)
        """
    )
    op.drop_column("business_settings", "custom_orders_inventory_category_id")
    op.drop_column("business_settings", "custom_orders_product_id")
    op.drop_column("business_settings", "custom_orders_branch_id")
    op.execute(
        f"""
        DELETE FROM inventory_categories c
        WHERE c.reference = '{_CATEGORY_REFERENCE}'
          AND NOT EXISTS (
              SELECT 1 FROM inventory_items i WHERE i.category_id = c.id
          )
        """
    )
