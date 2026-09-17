"""The Keeta billing-report parser locates columns by HEADER NAME, not a fixed
index — Keeta ships two "Order Summary" layouts (a 40-column form and a newer
38-column form that drops "Total Commission" and shifts every later column left),
and a fixed index silently mis-read one of them (booking "Product subsidies by
Keeta" as commission). These build both layouts and assert the parser reads the
right column, reconciles to net, and captures the merchant delivery subsidy.
"""

from __future__ import annotations

import io
from decimal import Decimal

import openpyxl

from app.services.providers.keeta_provider import _parse_bill_xlsx


def _bill(headers: dict[int, str], data: dict[int, object], ncol: int) -> bytes:
    """One-order Order Summary xlsx: rows 1-2 are group headers, row 3 the column
    header, row 4 the data — columns placed at their 1-indexed positions."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Order Summary"
    ws.append([])
    ws.append([])
    ws.append([headers.get(c) for c in range(1, ncol + 1)])
    ws.append([data.get(c) for c in range(1, ncol + 1)])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _by_cat(lines) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for ln in lines:
        out[ln.fee_category] = out.get(ln.fee_category, Decimal(0)) + ln.amount
    return out


# 40-column layout: carries "Total Commission" (col 35); col 38/39 are decoys the
# fixed-index parser used to read as bank/commission.
_H40 = {
    6: "Transaction date",
    9: "Order Number",
    16: "Original item price (VAT included)",
    21: "Subtotal of commission fee (VAT included)",
    22: "Bank fee (VAT included)",
    23: "Delivery subsidies (Keeta delivery) covered by merchant",
    33: "Payable to merchant",
    34: "Notes",
    35: "Total Commission(VAT included)",
    38: "Bank Fee Rate",
    39: "Product subsidies by Keeta(VAT included)",
}
_D40 = {
    6: "1 Sep 2026",
    9: "5097841643546126",
    16: 70.0,
    21: -16.5,
    22: -0.9,
    23: -4.0,
    33: 48.6,
    35: -16.5,
    38: "2%",
    39: 25.0,  # Keeta-borne subsidy — must be ignored, never read as commission
}

# 38-column layout: NO "Total Commission" column; col 35 is the Keeta subsidy decoy.
_H38 = {
    6: "Transaction date",
    9: "Order Number",
    16: "Original item price (VAT included)",
    20: "Subtotal of commission fee (VAT included)",
    21: "Bank fee (VAT included)",
    22: "Delivery subsidies (Keeta delivery) covered by merchant",
    30: "Payable to merchant",
    35: "Product subsidies by Keeta(VAT included)",
}
_D38 = {
    6: "8 Sep 2026",
    9: "5167840151623393",
    16: 40.0,
    20: -9.0,
    21: -0.5,
    22: -4.0,
    30: 26.5,
    35: 15.0,  # Keeta-borne subsidy decoy at the OLD commission column
}


def test_parses_40_column_layout_and_reconciles():
    cat = _by_cat(_parse_bill_xlsx(_bill(_H40, _D40, 40), "S"))
    assert cat["gross_sales"] == Decimal("70")
    assert cat["commission"] == Decimal("-16.5")  # from "Total Commission"
    assert cat["bank_fee"] == Decimal("-0.9")  # not "Bank Fee Rate"
    assert cat["net_payable"] == Decimal("48.6")
    assert cat["delivery_subsidy"] == Decimal("-4.0")  # merchant-borne, captured
    assert "gross_sales" in cat and 25.0 not in cat.values()  # Keeta subsidy ignored
    # gross + commission + bank + delivery_subsidy == net
    assert (
        cat["gross_sales"]
        + cat["commission"]
        + cat["bank_fee"]
        + cat["delivery_subsidy"]
        == cat["net_payable"]
    )


def test_parses_38_column_layout_without_total_commission():
    """The newer layout has no "Total Commission" — commission comes from the
    subtotal, and the parser must NOT read col 35 ("Product subsidies by Keeta")."""
    cat = _by_cat(_parse_bill_xlsx(_bill(_H38, _D38, 38), "S"))
    assert cat["gross_sales"] == Decimal("40")
    assert cat["commission"] == Decimal("-9.0")  # subtotal, NOT the +15 decoy
    assert cat["bank_fee"] == Decimal("-0.5")
    assert cat["net_payable"] == Decimal("26.5")
    assert cat["delivery_subsidy"] == Decimal("-4.0")
    assert Decimal("15") not in cat.values()  # Keeta subsidy never booked
    assert (
        cat["gross_sales"]
        + cat["commission"]
        + cat["bank_fee"]
        + cat["delivery_subsidy"]
        == cat["net_payable"]
    )


def test_commission_adds_min_topup_when_no_total_column():
    """Below the minimum, commission = subtotal + the min-commission top-up."""
    headers = dict(_H38)
    headers[19] = "Total Top-up to Minimum (VAT included)"
    data = dict(_D38)
    data[19] = -1.0
    cat = _by_cat(_parse_bill_xlsx(_bill(headers, data, 38), "S"))
    assert cat["commission"] == Decimal("-10.0")  # -9.0 subtotal + -1.0 top-up


def test_source_key_is_stable_per_order_and_category():
    lines = _parse_bill_xlsx(
        _bill(_H38, _D38, 38), "KEETA_BILL_1_2026-09-08_2026-09-14"
    )
    keys = {ln.fee_category: ln.source_key for ln in lines}
    assert (
        keys["commission"]
        == "KEETA_BILL_1_2026-09-08_2026-09-14:5167840151623393:commission"
    )
