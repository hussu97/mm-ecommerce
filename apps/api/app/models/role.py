from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin

# ─── Permission catalogue ─────────────────────────────────────────────────────
# Grouped for rendering the role editor; stored on Role.permissions as a flat
# list of slugs.
#
# This used to be the Foodics authority matrix copied whole — 108 slugs, of
# which 61 were enforced nowhere in the codebase. They granted authority over
# reservations (the tables were dropped in `080_drop_unbuilt_pos`), allergens,
# timed events, price tags, waiters and count sheets, none of which this
# business has; and they split rights nobody splits — two permissions for
# pressing print, four for reading a cost report, eleven `create`/`submit`
# pairs against inventory screens that have one button and one endpoint.
#
# The rule now: **a slug goes in the catalogue only if a route enforces it.**
# `test_permissions.py::test_every_permission_is_enforced` fails on any slug
# that no `require(...)`/`ensure(...)` in `app/` names, so the catalogue cannot
# drift back into a wish list. `PERMISSION_MIGRATION` below carries every slug
# that used to exist onto the one that replaced it.

PERMISSION_GROUPS: dict[str, list[tuple[str, str]]] = {
    "Orders": [
        ("orders.read", "View orders in the console and on the register"),
        ("orders.manage", "Accept, decline, void, pay and tag orders"),
        ("orders.custom.manage", "Manage custom cake orders"),
    ],
    "Catalogue": [
        # There is no `catalogue.read`: the product, category, modifier and
        # menu-group reads are the storefront's own endpoints and are open to
        # anyone with the URL. A permission to view them would gate nothing.
        ("catalogue.manage", "Create and edit products, categories and modifiers"),
        ("catalogue.recipes.read", "View recipes, ingredients and costs"),
        ("catalogue.recipes.manage", "Edit recipes, ingredients and costs"),
    ],
    "Inventory": [
        ("inventory.read", "View inventory items, suppliers and stock levels"),
        ("inventory.ledger.read", "View immutable inventory movement history"),
        ("inventory.manage", "Edit inventory items and suppliers"),
        (
            "inventory.adjustments.manage",
            "Adjust quantities and costs, count stock and record production",
        ),
        ("inventory.transfers.manage", "Create, send and receive stock transfers"),
        (
            "inventory.purchase_orders.manage",
            "Create and submit purchase orders, and purchase against them",
        ),
        # Kept apart from `.manage` on purpose: whoever raises the order must
        # not be the one who approves it.
        ("inventory.purchase_orders.approve", "Approve purchase orders"),
        ("inventory.reports.submit", "Submit and defer branch inventory reports"),
        ("inventory.counts.approve", "Approve stock counts and variances"),
        ("inventory.projection.rebuild", "Rebuild cached balances from the ledger"),
    ],
    "Customers": [
        # Likewise no `customers.manage`: the console lists customers and their
        # spend and has never had an edit or delete endpoint for them.
        ("customers.read", "View customers and their spend"),
    ],
    "Marketing & content": [
        ("marketing.manage", "Manage promo codes, coupons and campaigns"),
        ("content.manage", "Manage website content, blog, languages and translations"),
    ],
    "Delivery": [
        ("delivery.manage", "Manage delivery zones, couriers and dispatch"),
    ],
    "Reports": [
        ("reports.sales", "View sales reports"),
        ("reports.cost", "View cost analysis, menu cost and cost history reports"),
        ("reports.inventory", "View inventory level, control and transaction reports"),
        ("reports.other", "View all other reports"),
    ],
    "Dashboard": [
        ("dashboard.access", "Access the dashboard"),
    ],
    "Administration": [
        ("admin.branches.manage", "Manage branches"),
        ("admin.devices.manage", "Manage devices and printers"),
        (
            "admin.settings.manage",
            "Manage business settings, taxes, charges, reasons, tags, "
            "payment methods and kitchen flows",
        ),
        ("admin.users.manage", "Manage staff, users and roles"),
        ("admin.payments.manage", "Manage payment gateways"),
        ("admin.logs.read", "View the audit, email and webhook logs"),
        ("admin.data.manage", "Import and export data"),
    ],
    "Cashier app": [
        ("pos.register.access", "Access the cash register"),
        # No `pos.reports.access`: the terminal and the console read the same
        # report endpoints, and those are already gated per report by
        # `reports.*`. A second door in front of the same room is a door
        # somebody forgets to lock.
        ("pos.discounts.predefined", "Apply predefined discounts"),
        ("pos.discounts.open", "Apply open discounts"),
        ("pos.charges.open", "Add an open charge"),
        ("pos.orders.void", "Void an order or a single product"),
        ("pos.orders.return", "Return orders"),
        ("pos.orders.split_join", "Split and join orders"),
        (
            "pos.orders.edit_others",
            "Edit orders opened by other users, and take over a table",
        ),
        ("pos.payment.perform", "Take payment"),
        # Giving money back is not the same authority as taking it. Refunds were
        # gated on `pos.payment.perform` alone, so any cashier who could ring up
        # a sale could hand cash out of the drawer against any tender, with no
        # manager approval.
        ("pos.payment.refund", "Refund a payment"),
        ("pos.products.open_price", "Add an open-price product"),
        ("pos.products.availability", "Mark products out of stock at a branch"),
        (
            "pos.kitchen.manage",
            "Send to the kitchen before payment and edit sent items",
        ),
        ("pos.till.manage", "Open the drawer, spot check cash and close the till"),
        ("pos.driver.act_as", "Act as a delivery driver"),
    ],
}

ALL_PERMISSIONS: list[str] = [
    slug for group in PERMISSION_GROUPS.values() for slug, _ in group
]

PERMISSION_DESCRIPTIONS: dict[str, str] = {
    slug: description
    for group in PERMISSION_GROUPS.values()
    for slug, description in group
}

# ─── Retired slugs ────────────────────────────────────────────────────────────
# Every slug the catalogue ever held, mapped onto the one that replaced it, or
# to None where the feature behind it does not exist. Migration
# `105_consolidate_role_permissions` rewrites `roles.permissions` through this
# map in both directions, and `test_permissions.py` asserts it covers the whole
# old catalogue — so a live grant can never be silently dropped by a rename.
#
# Read a `None` as "this granted authority over nothing": the routes were never
# built, or (reservations, price tags, spot-check *stock* counts, table layouts)
# were dropped in `079`/`080` along with their tables.

PERMISSION_MIGRATION: dict[str, str | None] = {
    # Orders — tags were never a right of their own; editing an order covers it.
    "orders.tags.manage": "orders.manage",
    # Customers — spend is the reason you open a customer record at all.
    "customers.insights.read": "customers.read",
    # No customer edit endpoint has ever existed for this to have gated.
    "customers.manage": None,
    # Inventory — the console has one screen per noun, not a create/submit pair.
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
    "inventory.count_sheets.create": None,  # printed count sheets: never built
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
    # Menu became Catalogue: the storefront and the register read one catalogue.
    # Catalogue reads are the storefront's public endpoints; this gated nothing.
    "menu.read": None,
    "menu.manage": "catalogue.manage",
    "menu.items.hide": "catalogue.manage",
    "menu.items.out_of_stock": "pos.products.availability",
    "menu.ingredients.read": "catalogue.recipes.read",
    "menu.ingredients.manage": "catalogue.recipes.manage",
    "menu.costs.manage": "catalogue.recipes.manage",
    # Administration — one slug per console section that exists.
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
    "admin.reservations.manage": None,  # tables dropped in 080
    "admin.allergens.manage": None,  # never built
    "admin.timed_events.manage": None,  # never built
    # Reports — the POS reports page shows these together.
    "reports.cost_analysis": "reports.cost",
    "reports.menu_cost": "reports.cost",
    "reports.cost_adjustment_history": "reports.cost",
    "reports.inventory_items_cost": "reports.cost",
    "reports.inventory_levels": "reports.inventory",
    "reports.inventory_control": "reports.inventory",
    "reports.inventory_transactions": "reports.inventory",
    "reports.activity_log": "admin.logs.read",
    # Dashboard — three panels of one page.
    "dashboard.general": "dashboard.access",
    "dashboard.branches": "dashboard.access",
    "dashboard.inventory": "dashboard.access",
    # Cashier app.
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
    "pos.print.check": None,  # pressing print is not an authority
    "pos.print.receipt": None,
    "pos.orders.ahead": None,  # scheduling is what the register is for
    "pos.waiter.act_as": None,  # counter service; there are no waiters
    "pos.tables.edit_layout": None,  # tables dropped in 080
}


class Role(Base, UUIDMixin, TimestampMixin):
    """
    A named permission set assigned to staff. `is_super_admin` short-circuits every
    permission check so the owner account can never be locked out of its own console.
    """

    __tablename__ = "roles"

    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name_localized: Mapped[str | None] = mapped_column(String(100), nullable=True)
    translations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    is_super_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def has(self, permission: str) -> bool:
        return self.is_super_admin or permission in (self.permissions or [])

    def __repr__(self) -> str:
        return f"<Role {self.name}>"


class UserBranch(Base, UUIDMixin, TimestampMixin):
    """Which branches a staff member may sign in to."""

    __tablename__ = "user_branches"
    __table_args__ = (UniqueConstraint("user_id", "branch_id", name="uq_user_branch"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    branch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("branches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return f"<UserBranch user={self.user_id} branch={self.branch_id}>"
