"""Consolidate the role permission catalogue from 108 slugs to 43.

The catalogue in `app/models/role.py` was the Foodics authority matrix copied
whole. 61 of its 108 slugs were enforced by no route in the codebase: they
granted authority over reservations (whose tables went in `080_drop_unbuilt_pos`),
allergens, timed events, price tags, waiters and count sheets — none of which
this business has — and they split rights nobody splits, most visibly eleven
`create`/`submit` pairs against inventory screens with one button apiece.

This rewrites `roles.permissions` through `PERMISSION_MIGRATION`, which pairs
every retired slug with the one that replaced it. Nothing a live role could
actually do is taken away: the slugs that map to nothing are the ones that
gated nothing.

**Why this is a migration and not a `scripts/` file**, against the usual rule
that content edits live in scripts. The vocabulary of the column changed in the
same commit as the code that reads it. The moment the new API is live,
`POST /roles` rejects every old slug as unknown and `require("catalogue.manage")`
matches nothing any role holds — so a script somebody runs afterwards, or
forgets to, is a window in which every non-super role is silently inert. It has
to move with the deploy, which is what a migration is.

The map is duplicated here rather than imported from `app.models.role` on
purpose: a migration has to keep describing the database as it was on the day
it ran, and importing today's catalogue would make this file's behaviour change
every time the catalogue does.

Revision ID: 105_consolidate_role_perms
Revises: 104_branch_availability_expiry
Create Date: 2026-08-16
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "105_consolidate_role_perms"
down_revision: Union[str, None] = "104_branch_availability_expiry"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: Retired slug -> the slug that replaced it, or None where the feature behind
#: it was never built (or was dropped in 079/080 along with its tables).
FORWARD: dict[str, str | None] = {
    "orders.tags.manage": "orders.manage",
    "customers.insights.read": "customers.read",
    # No customer edit endpoint has ever existed for this to have gated.
    "customers.manage": None,
    "inventory.items.read": "inventory.read",
    "inventory.suppliers.read": "inventory.read",
    "inventory.order_transactions.read": "inventory.read",
    "inventory.items.manage": "inventory.manage",
    "inventory.suppliers.manage": "inventory.manage",
    "inventory.quantity_adjustment.create": "inventory.adjustments.manage",
    "inventory.quantity_adjustment.submit": "inventory.adjustments.manage",
    "inventory.cost_adjustment.create": "inventory.adjustments.manage",
    "inventory.cost_adjustment.submit": "inventory.adjustments.manage",
    "inventory.counts.create": "inventory.adjustments.manage",
    "inventory.counts.submit": "inventory.adjustments.manage",
    "inventory.production.create": "inventory.adjustments.manage",
    "inventory.production.submit": "inventory.adjustments.manage",
    "inventory.spot_check.create": "inventory.adjustments.manage",
    "inventory.spot_check.submit": "inventory.adjustments.manage",
    "inventory.count_sheets.create": None,
    "inventory.transfer_orders.create": "inventory.transfers.manage",
    "inventory.transfer_orders.submit": "inventory.transfers.manage",
    "inventory.transfers.create": "inventory.transfers.manage",
    "inventory.transfers.send_receive": "inventory.transfers.manage",
    "inventory.purchase_orders.create": "inventory.purchase_orders.manage",
    "inventory.purchase_orders.submit": "inventory.purchase_orders.manage",
    "inventory.purchase_orders.view_approved": "inventory.purchase_orders.manage",
    "inventory.purchasing.create": "inventory.purchase_orders.manage",
    "inventory.purchasing.submit": "inventory.purchase_orders.manage",
    "inventory.purchasing.from_po": "inventory.purchase_orders.manage",
    "inventory.purchasing.direct": "inventory.purchase_orders.manage",
    # Catalogue reads are the storefront's public endpoints; this gated nothing.
    "menu.read": None,
    "menu.manage": "catalogue.manage",
    "menu.items.hide": "catalogue.manage",
    "menu.items.out_of_stock": "pos.products.availability",
    "menu.ingredients.read": "catalogue.recipes.read",
    "menu.ingredients.manage": "catalogue.recipes.manage",
    "menu.costs.manage": "catalogue.recipes.manage",
    "admin.taxes.manage": "admin.settings.manage",
    "admin.charges.manage": "admin.settings.manage",
    "admin.reasons.manage": "admin.settings.manage",
    "admin.tags.manage": "admin.settings.manage",
    "admin.payment_methods.manage": "admin.settings.manage",
    "admin.kitchen_flows.manage": "admin.settings.manage",
    "admin.online_ordering.manage": "admin.settings.manage",
    "admin.notifications.manage": "admin.settings.manage",
    "admin.discounts.manage": "marketing.manage",
    "admin.coupons.manage": "marketing.manage",
    "admin.promotions.manage": "marketing.manage",
    "admin.delivery_zones.manage": "delivery.manage",
    "admin.drivers.manage": "delivery.manage",
    "admin.reservations.manage": None,
    "admin.allergens.manage": None,
    "admin.timed_events.manage": None,
    "reports.cost_analysis": "reports.cost",
    "reports.menu_cost": "reports.cost",
    "reports.cost_adjustment_history": "reports.cost",
    "reports.inventory_items_cost": "reports.cost",
    "reports.inventory_levels": "reports.inventory",
    "reports.inventory_control": "reports.inventory",
    "reports.inventory_transactions": "reports.inventory",
    "reports.activity_log": "admin.logs.read",
    "dashboard.general": "dashboard.access",
    "dashboard.branches": "dashboard.access",
    "dashboard.inventory": "dashboard.access",
    "pos.products.void": "pos.orders.void",
    "pos.orders.split": "pos.orders.split_join",
    "pos.orders.join": "pos.orders.split_join",
    "pos.orders.edit_online": "pos.orders.edit_others",
    "pos.tables.change_owner": "pos.orders.edit_others",
    "pos.orders.view_done": "orders.read",
    "pos.orders.tags": "orders.manage",
    "pos.kitchen.send_before_payment": "pos.kitchen.manage",
    "pos.kitchen.reprint": "pos.kitchen.manage",
    "pos.kitchen.edit_sent": "pos.kitchen.manage",
    "pos.drawer.access": "pos.till.manage",
    "pos.end_of_day": "pos.till.manage",
    "pos.spot_check": "pos.till.manage",
    "pos.till.close_with_active_orders": "pos.till.manage",
    "pos.orders.pay_without_closing": "pos.till.manage",
    "pos.devices.access": "admin.devices.manage",
    # The terminal reads the same report endpoints the console does.
    "pos.reports.access": "reports.sales",
    "pos.print.check": None,
    "pos.print.receipt": None,
    "pos.orders.ahead": None,
    "pos.waiter.act_as": None,
    "pos.tables.edit_layout": None,
}

#: The reverse is lossy by construction — several old slugs collapse onto one
#: new slug, so downgrading has to pick which ones to hand back. It hands back
#: *all* of them: the alternative is a role that silently loses a right on the
#: way down, and a downgrade is a rollback, where the safe failure is the
#: console still working rather than a manager locked out mid-service.
#: `admin.reservations.manage` and the other dropped-feature slugs are not
#: restored — there is nothing behind them to restore access to.
BACKWARD: dict[str, list[str]] = {}
for _old, _new in FORWARD.items():
    if _new is not None:
        BACKWARD.setdefault(_new, []).append(_old)

#: New slugs that no old slug maps onto, so `BACKWARD` cannot name them. They
#: have to be dropped explicitly on the way down — the old
#: `_validate_permissions` rejects any slug outside the old catalogue, so a role
#: still holding one of these would fail the first time somebody opened it in
#: the role editor. Everything else the map does not mention (`orders.read`,
#: `pos.register.access`, `reports.sales`, …) exists in *both* catalogues and is
#: left exactly as it is.
NEW_ONLY: set[str] = {
    "orders.custom.manage",
    "content.manage",
    "admin.payments.manage",
    "admin.data.manage",
}


def _remap(mapping: dict[str, list[str]], *, drop: set[str] = frozenset()) -> None:
    """Rewrite every role's permission array through *mapping*, de-duplicated.

    Anything neither mapped nor in *drop* is carried across untouched.
    """
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, permissions FROM roles")).fetchall()
    for role_id, permissions in rows:
        out: list[str] = []
        for slug in permissions or []:
            if slug in mapping:
                out.extend(mapping[slug])
            elif slug not in drop:
                out.append(slug)
        # dict.fromkeys keeps first-seen order, which keeps the diff readable
        # for anyone eyeballing the column after the deploy.
        deduped = list(dict.fromkeys(out))
        conn.execute(
            sa.text("UPDATE roles SET permissions = :p WHERE id = :i"),
            {"p": deduped, "i": role_id},
        )


def upgrade() -> None:
    # A slug mapping to None disappears; a slug already in the new catalogue
    # (orders.read, pos.register.access, reports.sales, …) is left alone.
    _remap({old: ([new] if new else []) for old, new in FORWARD.items()})


def downgrade() -> None:
    _remap(BACKWARD, drop=NEW_ONLY)
