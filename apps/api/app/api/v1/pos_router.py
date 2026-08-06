"""
The surface the register talks to.

Deliberately narrower than the main API. A terminal sits on a shop counter and
authenticates with a device token, so anything it does not need — the
storefront cart, blog and CMS, customer accounts, admin imports and exports —
should not be reachable from it at all. What is left is the menu it renders,
the order it builds, the money it takes, and the hardware it drives.

Config endpoints appear here in their read-only sense: the same routers back
the console, and the write paths on them are already gated behind an admin
user, which a device token is not.
"""

from fastapi import APIRouter

from .branches import router as branches_router
from .business_settings import router as business_settings_router
from .categories import router as categories_router
from .devices import printers_router, router as devices_router
from .marketing import (
    discounts_router,
    promotions_router,
    timed_events_router,
)
from .menu_groups import router as menu_groups_router
from .modifiers import router as modifiers_router
from .operations import dashboard_router
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
from .pos_orders import kitchen_router, router as pos_orders_router
from .pos_reports import router as pos_reports_router
from .products import router as products_router
from .staff import router as staff_router
from .tills import router as tills_router

pos_api_router = APIRouter()

# ─── Pairing and sign-in ──────────────────────────────────────────────────────
pos_api_router.include_router(devices_router, prefix="/devices", tags=["Devices"])
pos_api_router.include_router(staff_router, prefix="/staff", tags=["Staff"])
pos_api_router.include_router(branches_router, prefix="/branches", tags=["Branches"])
pos_api_router.include_router(
    business_settings_router, prefix="/business-settings", tags=["Business Settings"]
)

# ─── The menu ─────────────────────────────────────────────────────────────────
pos_api_router.include_router(products_router, prefix="/products", tags=["Products"])
pos_api_router.include_router(
    categories_router, prefix="/categories", tags=["Categories"]
)
pos_api_router.include_router(modifiers_router, prefix="/modifiers", tags=["Modifiers"])
pos_api_router.include_router(menu_groups_router, prefix="/menu-groups", tags=["Menu"])
pos_api_router.include_router(courses_router, prefix="/courses", tags=["Menu"])

# ─── Selling ──────────────────────────────────────────────────────────────────
pos_api_router.include_router(
    pos_orders_router, prefix="/pos/orders", tags=["POS Orders"]
)
pos_api_router.include_router(kitchen_router, prefix="/pos/kitchen", tags=["Kitchen"])
pos_api_router.include_router(tills_router, prefix="/tills", tags=["Tills"])
pos_api_router.include_router(
    payment_methods_router, prefix="/payment-methods", tags=["Payment Methods"]
)
pos_api_router.include_router(charges_router, prefix="/charges", tags=["Charges"])
pos_api_router.include_router(reasons_router, prefix="/reasons", tags=["Reasons"])
pos_api_router.include_router(taxes_router, prefix="/taxes", tags=["Taxes"])
pos_api_router.include_router(tax_groups_router, prefix="/tax-groups", tags=["Taxes"])
pos_api_router.include_router(tags_router, prefix="/tags", tags=["Tags"])

# ─── Money off ────────────────────────────────────────────────────────────────
pos_api_router.include_router(discounts_router, prefix="/discounts", tags=["Marketing"])
pos_api_router.include_router(
    promotions_router, prefix="/promotions", tags=["Marketing"]
)
pos_api_router.include_router(
    timed_events_router, prefix="/timed-events", tags=["Marketing"]
)

# ─── Hardware and the floor ───────────────────────────────────────────────────
pos_api_router.include_router(printers_router, prefix="/printers", tags=["Printers"])
pos_api_router.include_router(
    kitchen_flows_router, prefix="/kitchen-flows", tags=["Kitchen Flows"]
)

# ─── What a shift lead looks at ───────────────────────────────────────────────
pos_api_router.include_router(
    pos_reports_router, prefix="/pos/reports", tags=["POS Reports"]
)
pos_api_router.include_router(
    dashboard_router, prefix="/pos/dashboard", tags=["POS Dashboard"]
)
