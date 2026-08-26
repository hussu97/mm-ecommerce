from fastapi import APIRouter

from .addresses import router as addresses_router
from .aggregators import router as aggregators_router
from .analytics import router as analytics_router
from .audit_logs import router as audit_logs_router
from .auth import router as auth_router
from .blog import router as blog_router

# POS domain
from .branches import router as branches_router
from .bulk import router as bulk_router
from .business_settings import router as business_settings_router
from .cart import router as cart_router
from .categories import router as categories_router
from .cms import router as cms_router
from .custom_orders import (
    admin_router as custom_orders_admin_router,
)
from .custom_orders import (
    router as custom_orders_router,
)
from .dashboard import router as home_dashboard_router
from .delivery import router as delivery_router
from .delivery_zones import router as delivery_zones_router
from .devices import printers_router
from .devices import router as devices_router
from .email_logs import router as email_logs_router
from .export_data import router as export_router
from .grubops import router as grubops_router
from .i18n import router as i18n_router
from .import_data import router as import_router
from .inventory import (
    categories_router as inventory_categories_router,
)
from .inventory import (
    counts_router as inventory_counts_router,
)
from .inventory import (
    items_router as inventory_items_router,
)
from .inventory import (
    levels_router as inventory_levels_router,
)
from .inventory import (
    purchase_orders_router,
    recipes_router,
    suppliers_router,
    warehouses_router,
)
from .inventory import (
    transactions_router as inventory_transactions_router,
)
from .marketing import (
    discounts_router,
    promotions_router,
    timed_events_router,
)
from .menu_groups import router as menu_groups_router
from .modifiers import router as modifiers_router
from .operations import (
    dashboard_router,
    notification_rules_router,
    production_router,
    transfer_orders_router,
)
from .orders import router as orders_router
from .payment_gateways import router as payment_gateways_router
from .payments import router as payments_router
from .pos_config import (
    charges_router,
    courses_router,
    kitchen_flows_router,
    payment_methods_router,
    reasons_router,
    tags_router,
    tax_groups_router,
    taxes_router,
)
from .pos_orders import kitchen_router
from .pos_orders import router as pos_orders_router
from .pos_reports import router as pos_reports_router
from .products import router as products_router
from .promo_codes import router as promo_codes_router
from .redirects import router as redirects_router
from .staff import roles_router
from .staff import router as staff_router
from .tills import router as tills_router
from .uploads import router as uploads_router
from .users import router as users_router
from .webhook_logs import router as webhook_logs_router
from .webhooks import router as webhooks_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
api_router.include_router(categories_router, prefix="/categories", tags=["Categories"])
api_router.include_router(redirects_router, prefix="/redirects", tags=["Redirects"])
api_router.include_router(products_router, prefix="/products", tags=["Products"])
api_router.include_router(grubops_router, prefix="/grubops", tags=["GrubOps"])
api_router.include_router(
    aggregators_router, prefix="/aggregators", tags=["Aggregators"]
)
api_router.include_router(modifiers_router, prefix="/modifiers", tags=["Modifiers"])
api_router.include_router(import_router, prefix="/import", tags=["Import"])
api_router.include_router(cart_router, prefix="/cart", tags=["Cart"])
api_router.include_router(uploads_router, prefix="/uploads", tags=["Uploads"])
api_router.include_router(delivery_router, prefix="/delivery", tags=["Delivery"])
api_router.include_router(
    delivery_zones_router, prefix="/delivery-zones", tags=["Delivery Zones"]
)
api_router.include_router(
    promo_codes_router, prefix="/promo-codes", tags=["Promo Codes"]
)
api_router.include_router(orders_router, prefix="/orders", tags=["Orders"])
api_router.include_router(addresses_router, prefix="/addresses", tags=["Addresses"])
api_router.include_router(payments_router, prefix="/payments", tags=["Payments"])
api_router.include_router(
    payment_gateways_router, prefix="/payment-gateways", tags=["Payments"]
)
api_router.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])
api_router.include_router(analytics_router, prefix="/analytics", tags=["Analytics"])
api_router.include_router(
    home_dashboard_router, prefix="/dashboard", tags=["Dashboard"]
)
api_router.include_router(users_router, prefix="/users", tags=["Users"])
api_router.include_router(bulk_router, prefix="/bulk", tags=["Bulk"])
api_router.include_router(export_router, prefix="/export", tags=["Export"])
api_router.include_router(i18n_router, prefix="/i18n", tags=["i18n"])
api_router.include_router(cms_router, prefix="/cms", tags=["CMS"])
api_router.include_router(
    custom_orders_router, prefix="/custom-orders", tags=["Custom Orders"]
)
api_router.include_router(
    custom_orders_admin_router,
    prefix="/admin/custom-orders",
    tags=["Custom Orders"],
)
api_router.include_router(blog_router, prefix="/blog", tags=["Blog"])
api_router.include_router(email_logs_router, prefix="/email-logs", tags=["Email Logs"])
api_router.include_router(audit_logs_router, prefix="/audit-logs", tags=["Audit Logs"])
api_router.include_router(
    webhook_logs_router, prefix="/webhook-logs", tags=["Webhook Logs"]
)

# ─── POS ──────────────────────────────────────────────────────────────────────
api_router.include_router(branches_router, prefix="/branches", tags=["Branches"])
api_router.include_router(taxes_router, prefix="/taxes", tags=["Taxes"])
api_router.include_router(tax_groups_router, prefix="/tax-groups", tags=["Taxes"])
api_router.include_router(
    payment_methods_router, prefix="/payment-methods", tags=["Payment Methods"]
)
api_router.include_router(charges_router, prefix="/charges", tags=["Charges"])
api_router.include_router(reasons_router, prefix="/reasons", tags=["Reasons"])
api_router.include_router(courses_router, prefix="/courses", tags=["Menu"])
api_router.include_router(menu_groups_router, prefix="/menu-groups", tags=["Menu"])
api_router.include_router(tags_router, prefix="/tags", tags=["Tags"])
api_router.include_router(
    kitchen_flows_router, prefix="/kitchen-flows", tags=["Kitchen Flows"]
)
api_router.include_router(devices_router, prefix="/devices", tags=["Devices"])
api_router.include_router(printers_router, prefix="/printers", tags=["Printers"])
api_router.include_router(roles_router, prefix="/roles", tags=["Roles"])
api_router.include_router(staff_router, prefix="/staff", tags=["Staff"])
api_router.include_router(tills_router, prefix="/tills", tags=["Tills"])
api_router.include_router(
    business_settings_router, prefix="/business-settings", tags=["Business Settings"]
)
api_router.include_router(pos_orders_router, prefix="/pos/orders", tags=["POS Orders"])
api_router.include_router(kitchen_router, prefix="/pos/kitchen", tags=["Kitchen"])
api_router.include_router(
    pos_reports_router, prefix="/pos/reports", tags=["POS Reports"]
)

# ─── Inventory ────────────────────────────────────────────────────────────────
api_router.include_router(
    inventory_categories_router,
    prefix="/inventory/categories",
    tags=["Inventory"],
)
api_router.include_router(
    inventory_items_router, prefix="/inventory/items", tags=["Inventory"]
)
api_router.include_router(
    inventory_levels_router, prefix="/inventory/levels", tags=["Inventory"]
)
api_router.include_router(
    inventory_transactions_router,
    prefix="/inventory/transactions",
    tags=["Inventory"],
)
api_router.include_router(
    inventory_counts_router, prefix="/inventory/counts", tags=["Inventory"]
)
api_router.include_router(
    warehouses_router, prefix="/inventory/warehouses", tags=["Inventory"]
)
api_router.include_router(
    suppliers_router, prefix="/inventory/suppliers", tags=["Inventory"]
)
api_router.include_router(
    purchase_orders_router,
    prefix="/inventory/purchase-orders",
    tags=["Inventory"],
)
api_router.include_router(
    recipes_router, prefix="/inventory/recipes", tags=["Inventory"]
)

# ─── Marketing ────────────────────────────────────────────────────────────────
api_router.include_router(discounts_router, prefix="/discounts", tags=["Marketing"])
api_router.include_router(promotions_router, prefix="/promotions", tags=["Marketing"])
api_router.include_router(
    timed_events_router, prefix="/timed-events", tags=["Marketing"]
)

# ─── Operations ───────────────────────────────────────────────────────────────
api_router.include_router(
    transfer_orders_router, prefix="/inventory/transfer-orders", tags=["Inventory"]
)
api_router.include_router(
    production_router, prefix="/inventory/production", tags=["Inventory"]
)
api_router.include_router(
    notification_rules_router, prefix="/notification-rules", tags=["Notifications"]
)
api_router.include_router(
    dashboard_router, prefix="/pos/dashboard", tags=["POS Dashboard"]
)
