"""The P&L's purchase-order table: the supplier paid most first."""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.api.v1.profit_loss import _biggest_supplier_first
from app.services.orders.pnl_purchase_orders import PoStockRow


def _po(ref: str, supplier: str, day: str, received: str) -> PoStockRow:
    return PoStockRow(uuid.uuid4(), ref, day, supplier, "Barsha", Decimal(received))


def test_the_biggest_supplier_leads_with_its_pos_together_and_biggest_first():
    rows = [
        _po("PO-1", "Waynik", "2026-10-03", "900"),
        _po("PO-2", "Bakers Village", "2026-10-02", "500"),
        _po("PO-3", "Bakers Village", "2026-10-09", "700"),
        _po("PO-4", "MH Enterprise", "2026-10-05", "300"),
        _po("PO-5", "MH Enterprise", "2026-10-07", "300"),
    ]
    ordered = [r.reference for r in _biggest_supplier_first(rows)]
    # Bakers Village 1200 > Waynik 900 > MH Enterprise 600; within a supplier
    # the bigger PO first, and the newer of two equal ones.
    assert ordered == ["PO-3", "PO-2", "PO-1", "PO-5", "PO-4"]
