from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin

# ─── Permission catalogue ─────────────────────────────────────────────────────
# Mirrors the Foodics role authority matrix. Grouped for rendering the role editor;
# stored on Role.permissions as a flat list of slugs.

PERMISSION_GROUPS: dict[str, list[tuple[str, str]]] = {
    "Orders": [
        ("orders.read", "View orders in the console"),
        ("orders.manage", "Accept, decline, void and pay orders in the console"),
        ("orders.tags.manage", "Edit order tags"),
    ],
    "Customers": [
        ("customers.read", "View customer data"),
        ("customers.insights.read", "View customer spend"),
        ("customers.manage", "Edit or delete customers"),
    ],
    "Inventory": [
        ("inventory.items.read", "View inventory items"),
        ("inventory.items.manage", "Edit or delete inventory items"),
        ("inventory.suppliers.read", "View suppliers"),
        ("inventory.suppliers.manage", "Edit or delete suppliers"),
        ("inventory.purchase_orders.create", "Create purchase orders"),
        ("inventory.purchase_orders.submit", "Submit purchase orders for approval"),
        ("inventory.purchase_orders.approve", "Approve purchase orders"),
        ("inventory.purchase_orders.view_approved", "View approved purchase orders"),
        ("inventory.transfer_orders.create", "Create transfer orders"),
        ("inventory.transfer_orders.submit", "Submit transfer orders"),
        ("inventory.transfers.create", "Create transfers"),
        ("inventory.transfers.send_receive", "Send and receive transfers"),
        ("inventory.purchasing.create", "Create purchasing transactions"),
        ("inventory.purchasing.submit", "Submit purchasing transactions"),
        ("inventory.purchasing.from_po", "Create purchasing from a purchase order"),
        ("inventory.purchasing.direct", "Create direct purchasing"),
        ("inventory.production.create", "Create production transactions"),
        ("inventory.production.submit", "Submit production transactions"),
        ("inventory.quantity_adjustment.create", "Create quantity adjustments"),
        ("inventory.quantity_adjustment.submit", "Submit quantity adjustments"),
        ("inventory.cost_adjustment.create", "Create cost adjustments"),
        ("inventory.cost_adjustment.submit", "Submit cost adjustments"),
        ("inventory.counts.create", "Create inventory counts"),
        ("inventory.counts.submit", "Submit inventory counts"),
        ("inventory.order_transactions.read", "View order-driven stock transactions"),
        ("inventory.spot_check.create", "Create inventory spot checks"),
        ("inventory.spot_check.submit", "Submit inventory spot checks"),
        ("inventory.count_sheets.create", "Create count sheets"),
    ],
    "Menu": [
        ("menu.read", "View the menu"),
        ("menu.manage", "Create and edit menu entities"),
        ("menu.items.hide", "Hide items from the menu"),
        ("menu.items.out_of_stock", "Mark items out of stock"),
        ("menu.ingredients.read", "View recipes and ingredients"),
        ("menu.ingredients.manage", "Edit recipes and ingredients"),
        ("menu.costs.manage", "Edit product and item costs"),
    ],
    "Administration": [
        ("admin.branches.manage", "Manage branches"),
        ("admin.devices.manage", "Manage devices and printers"),
        ("admin.discounts.manage", "Manage discounts"),
        ("admin.settings.manage", "Manage business settings"),
        ("admin.users.manage", "Manage users and roles"),
        ("admin.taxes.manage", "Manage taxes and tax groups"),
        ("admin.reservations.manage", "Manage reservations"),
        ("admin.payment_methods.manage", "Manage payment methods"),
        ("admin.charges.manage", "Manage charges"),
        ("admin.tags.manage", "Manage tags"),
        ("admin.reasons.manage", "Manage reasons"),
        ("admin.online_ordering.manage", "Manage online ordering"),
        ("admin.notifications.manage", "Manage notifications"),
        ("admin.allergens.manage", "Manage allergens"),
        ("admin.coupons.manage", "Manage coupons"),
        ("admin.promotions.manage", "Manage promotions"),
        ("admin.timed_events.manage", "Manage timed events"),
        ("admin.delivery_zones.manage", "Manage delivery zones"),
        ("admin.price_tags.manage", "Manage price tags"),
        ("admin.kitchen_flows.manage", "Manage kitchen flows"),
        ("admin.drivers.manage", "Manage delivery drivers"),
    ],
    "Reports": [
        ("reports.sales", "View sales reports"),
        ("reports.cost_analysis", "View cost analysis reports"),
        ("reports.inventory_control", "View inventory control reports"),
        ("reports.inventory_levels", "View inventory level reports"),
        ("reports.inventory_transactions", "View inventory transaction reports"),
        ("reports.cost_adjustment_history", "View cost adjustment history"),
        ("reports.menu_cost", "View menu cost reports"),
        ("reports.inventory_items_cost", "View inventory item cost reports"),
        ("reports.activity_log", "View the activity log"),
        ("reports.other", "View all other reports"),
    ],
    "Dashboard": [
        ("dashboard.general", "Access the general dashboard"),
        ("dashboard.branches", "Access the branches dashboard"),
        ("dashboard.inventory", "Access the inventory dashboard"),
    ],
    "Cashier app": [
        ("pos.register.access", "Access the cash register"),
        ("pos.devices.access", "Access device management on the terminal"),
        ("pos.reports.access", "Access reports on the terminal"),
        ("pos.discounts.predefined", "Apply predefined discounts"),
        ("pos.discounts.open", "Apply open discounts"),
        ("pos.charges.open", "Add an open charge"),
        ("pos.orders.join", "Join orders"),
        ("pos.drawer.access", "Access drawer operations"),
        ("pos.end_of_day", "Perform end of day"),
        ("pos.print.check", "Print the check"),
        ("pos.print.receipt", "Print the receipt"),
        ("pos.orders.return", "Return orders"),
        ("pos.orders.split", "Split orders"),
        ("pos.orders.view_done", "View completed orders"),
        ("pos.orders.void", "Void orders and products"),
        ("pos.products.void", "Void a single product"),
        ("pos.payment.perform", "Take payment"),
        # Giving money back is not the same authority as taking it. Refunds were
        # gated on `pos.payment.perform` alone, so any cashier who could ring up
        # a sale could hand cash out of the drawer against any tender, with no
        # manager approval.
        ("pos.payment.refund", "Refund a payment"),
        ("pos.orders.edit_others", "Edit orders opened by other users"),
        ("pos.orders.edit_online", "Edit online orders"),
        ("pos.tables.change_owner", "Change the table owner"),
        ("pos.kitchen.send_before_payment", "Send to kitchen before payment"),
        ("pos.kitchen.reprint", "Reprint kitchen tickets"),
        ("pos.kitchen.edit_sent", "Edit products already sent to the kitchen"),
        ("pos.till.close_with_active_orders", "Close the till with active orders"),
        ("pos.orders.pay_without_closing", "Fully pay orders without closing them"),
        ("pos.products.availability", "Manage product availability"),
        ("pos.orders.tags", "Manage tags on orders"),
        ("pos.orders.ahead", "Place ahead (scheduled) orders"),
        ("pos.driver.act_as", "Act as a delivery driver"),
        ("pos.waiter.act_as", "Act as a waiter"),
        ("pos.spot_check", "Perform a cash spot check"),
        ("pos.products.open_price", "Add an open-price product"),
        ("pos.tables.edit_layout", "Edit the table layout"),
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
