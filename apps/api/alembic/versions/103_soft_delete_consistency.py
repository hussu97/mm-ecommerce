"""A deleted row may not also claim to be active.

The POS tables soft-delete with a pair: `is_active` says whether the thing is
offered, `deleted_at` says it is gone for good. Every list screen filters on
the first and every delete sets both — by convention. Nothing constrained the
pair, so one UPDATE that sets `deleted_at` without flipping `is_active` creates
a ghost: a charge, a reason, a payment method that is deleted in the audit
trail and still on offer at the register.

The rule is one implication — `deleted_at IS NOT NULL ⇒ is_active = false` —
written here as its CHECK form. The other direction stays legal on purpose:
inactive-but-not-deleted is the ordinary "switched off for now" state half the
admin toggles exist to produce.

Applied to every table that carries both columns. `devices` and `tags` carry
`deleted_at` without `is_active` (devices have a status lifecycle instead) and
have nothing to keep consistent; the catalog tables (`products`, `categories`)
are deactivate-only — `is_active` with no `deleted_at` — which is a documented
choice, not a gap.

The repair UPDATE runs first and resolves any existing ghost the way every
reader already treats it: a deleted row is not active, whatever the flag said.

Revision ID: 103_soft_delete_consistency
Revises: 102_order_items_product_id_idx
Create Date: 2026-08-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "103_soft_delete_consistency"
down_revision: Union[str, None] = "102_order_items_product_id_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: Every table pairing `is_active` with `deleted_at`, read off the models.
TABLES: tuple[str, ...] = (
    "allergens",
    "branches",
    "charges",
    "combos",
    "discounts",
    "inventory_categories",
    "inventory_items",
    "kitchen_flows",
    "menu_groups",
    "notification_rules",
    "payment_methods",
    "printers",
    "promotions",
    "reasons",
    "roles",
    "sections",
    "suppliers",
    "tables",
    "tax_groups",
    "taxes",
    "timed_events",
    "warehouses",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(
            f"UPDATE {table} SET is_active = false "
            "WHERE deleted_at IS NOT NULL AND is_active"
        )
        op.create_check_constraint(
            f"ck_{table}_deleted_means_inactive",
            table,
            "deleted_at IS NULL OR is_active = false",
        )


def downgrade() -> None:
    # The repaired rows stay repaired: they were wrong before this migration
    # and the repair is not something a rollback should undo.
    for table in reversed(TABLES):
        op.drop_constraint(f"ck_{table}_deleted_means_inactive", table, type_="check")
