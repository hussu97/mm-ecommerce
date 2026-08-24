"""
POS reporting.

Every report is scoped by `business_date` rather than by `created_at`, so a
trading day that runs past midnight reports as one day. Only closed orders count
toward sales; open checks are work in progress, and voided ones are reported
separately rather than netted silently into the totals.

This was a single 1,805-line module — the largest file in the repository — and
it split cleanly because every function here is a pure read: `(db, scope) ->
rows`, no module state, nothing mutated. The five groups share `_scope` and the
label lookups in `_base` and nothing else.

**The barrel re-exports the report functions on purpose**, unlike
`app/services/__init__.py`, and the criterion is stated so it does not drift:
everything named here is called by `app/api/v1/pos_reports.py`, and nothing
else is. A helper that only its own group uses stays in its module.
"""

from ._base import SUPPORTED_DIMENSIONS
from .financial import (
    drawer_operations_report,
    payments_report,
    tax_report,
    tills_report,
    voids_and_returns,
)
from .operations import (
    branches_trend,
    menu_engineering,
    sales_predictions,
    speed_of_service,
    table_utilization,
)
from .sales import sales_by_dimension, sales_summary
from .stock import (
    cost_adjustment_history,
    cost_of_goods,
    inventory_valuation,
    purchase_orders_report,
    suppliers_analysis,
    transfers_report,
)

__all__ = [
    "SUPPORTED_DIMENSIONS",
    "branches_trend",
    "cost_adjustment_history",
    "cost_of_goods",
    "drawer_operations_report",
    "inventory_valuation",
    "menu_engineering",
    "payments_report",
    "purchase_orders_report",
    "sales_by_dimension",
    "sales_predictions",
    "sales_summary",
    "speed_of_service",
    "suppliers_analysis",
    "table_utilization",
    "tax_report",
    "tills_report",
    "transfers_report",
    "voids_and_returns",
]
