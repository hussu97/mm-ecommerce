"""The daily sales email: aggregation, the delivered filter, and the sheet."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal as D
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from openpyxl import load_workbook
from sqlalchemy.dialects import postgresql

from app.services.pos import daily_sales_email as dse

# ── Channel → column ─────────────────────────────────────────────────────────


def test_each_marketplace_maps_to_its_own_fixed_column():
    assert dse._column_for("aggregator", "Talabat") == "talabat"
    assert dse._column_for("aggregator", "Keeta 2.0") == "keeta"
    assert dse._column_for("aggregator", "Noon Food") == "noon_food"
    assert dse._column_for("aggregator", "Careem") == "careem"
    assert dse._column_for("aggregator", "Deliveroo") == "deliveroo"


def test_website_and_counter_are_their_own_columns():
    assert dse._column_for("online", None) == "website"
    assert dse._column_for("cashier", None) == "counter"


def test_an_unmapped_marketplace_keeps_its_name_rather_than_vanishing():
    # A new aggregator nobody has mapped is real money — it gets a column, not a
    # silent drop into nothing.
    assert dse._column_for("aggregator", "Brand New Co") == "Brand New Co"


# ── The delivered-only filter ────────────────────────────────────────────────


def test_the_filter_is_delivered_trade_only():
    sql = str(
        dse._DELIVERED.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    # A counter check closed, or a marketplace/website order delivered.
    assert "'cashier'" in sql and "'closed'" in sql
    assert "'delivered'" in sql
    assert "'aggregator'" in sql and "'online'" in sql


def test_the_fetch_query_compiles_to_postgres():
    import asyncio

    class _Cap:
        async def execute(self, stmt):
            str(stmt.compile(dialect=postgresql.dialect()))

            class _R:
                def all(self_inner):
                    return []

            return _R()

    asyncio.run(dse._fetch(_Cap(), "2026-08-24", "2026-08-24"))


# ── build() ──────────────────────────────────────────────────────────────────

_SHJ = uuid.uuid4()
_BAR = uuid.uuid4()


def _stub_db(rows):
    branches = [
        SimpleNamespace(id=_SHJ, name="Sharjah Kitchen"),
        SimpleNamespace(id=_BAR, name="Barsha Heights"),
    ]

    class _Res:
        def __init__(self, rows=None, scal=None):
            self._rows, self._scal = rows, scal

        def all(self):
            return self._rows

        def scalars(self):
            outer = self

            class _S:
                def all(self_inner):
                    return outer._scal

            return _S()

    class _DB:
        def __init__(self):
            self.n = 0

        async def execute(self, stmt):
            self.n += 1
            return _Res(rows=rows) if self.n == 1 else _Res(scal=branches)

    return _DB()


def _row(branch, source, channel, cnt, rev, disc, agg, pay, courier, refund):
    return (
        "2026-08-24",
        branch,
        source,
        channel,
        cnt,
        D(rev),
        D(disc),
        D(agg),
        D(pay),
        D(courier),
        D(refund),
    )


@pytest.mark.asyncio
async def test_build_lays_out_fixed_columns_and_zero_fills():
    rows = [
        _row(_SHJ, "aggregator", "Talabat", 44, "2680", "0", "800", "100", "0", "0"),
        _row(_SHJ, "online", None, 11, "750.50", "254.50", "0", "30", "391.59", "0"),
        _row(_SHJ, "cashier", None, 1, "35", "0", "0", "0", "0", "0"),
        _row(_BAR, "aggregator", "Careem", 2, "210", "0", "55", "4", "0", "0"),
    ]
    report = await dse.build(
        _stub_db(rows), date_from="2026-08-24", date_to="2026-08-24"
    )

    assert report.columns == [
        "keeta",
        "noon_food",
        "talabat",
        "careem",
        "deliveroo",
        "website",
        "counter",
    ]
    # Both active branches appear, ordered by name; Barsha's untouched columns
    # are zero, not missing.
    by_branch = {r.branch_name: r for r in report.rows}
    assert set(by_branch) == {"Sharjah Kitchen", "Barsha Heights"}
    assert by_branch["Barsha Heights"].cells["careem"].count == 2
    assert by_branch["Barsha Heights"].cells["talabat"].revenue == 0
    assert by_branch["Sharjah Kitchen"].cells["counter"].revenue == D("35")


@pytest.mark.asyncio
async def test_net_is_revenue_less_charges_less_refunds_and_charges_sum_all_three():
    rows = [_row(_SHJ, "online", None, 11, "750.50", "0", "0", "30", "391.59", "10")]
    report = await dse.build(
        _stub_db(rows), date_from="2026-08-24", date_to="2026-08-24"
    )
    cell = {r.branch_name: r for r in report.rows}["Sharjah Kitchen"].cells["website"]
    # charges = aggregator_fee + payment_fee + courier_cost
    assert cell.charges == D("421.59")
    # net = revenue - charges - refunds
    assert cell.net == D("750.50") - D("421.59") - D("10")


@pytest.mark.asyncio
async def test_xlsx_has_the_five_sections_with_discount_and_charges_negative():
    import io

    rows = [
        _row(_SHJ, "online", None, 11, "750.50", "254.50", "0", "30", "391.59", "0"),
        _row(_SHJ, "aggregator", "Talabat", 44, "2680", "0", "800", "100", "0", "0"),
    ]
    report = await dse.build(
        _stub_db(rows), date_from="2026-08-24", date_to="2026-08-24"
    )
    wb = load_workbook(io.BytesIO(dse.to_xlsx(report)))
    ws = wb.active
    titles = [
        row[0]
        for row in ws.iter_rows(values_only=True)
        if row[0] in {s[0] for s in dse._SECTIONS}
    ]
    assert titles == [
        "Sales Revenue",
        "Order Count",
        "Sales Discount",
        "Charges",
        "Net Revenue",
    ]

    # Find the Sharjah website discount cell under the Sales Discount block.
    values = list(ws.iter_rows(values_only=True))
    disc_idx = next(i for i, r in enumerate(values) if r[0] == "Sales Discount")
    header = values[disc_idx + 1]
    website_col = header.index("website")
    shj = next(r for r in values[disc_idx:] if r[1] == "Sharjah Kitchen")
    assert shj[website_col] == -254.5  # discount shown negative

    charges_idx = next(i for i, r in enumerate(values) if r[0] == "Charges")
    shj_charges = next(r for r in values[charges_idx:] if r[1] == "Sharjah Kitchen")
    assert shj_charges[header.index("talabat")] == -900.0  # 800 + 100, negative


# ── Scheduling helper ────────────────────────────────────────────────────────


def test_a_past_midnight_close_belongs_to_the_next_day():
    tz = ZoneInfo("Asia/Dubai")
    # Opens 10:00, closes 02:00 → the close is the following calendar day.
    branch = SimpleNamespace(opening_from="10:00", opening_to="02:00")
    close = dse._close_datetime(branch, datetime(2026, 8, 24, tzinfo=tz).date(), tz)
    assert close.day == 25 and close.hour == 2


def test_a_normal_evening_close_is_the_same_day():
    tz = ZoneInfo("Asia/Dubai")
    branch = SimpleNamespace(opening_from="10:00", opening_to="23:00")
    close = dse._close_datetime(branch, datetime(2026, 8, 24, tzinfo=tz).date(), tz)
    assert close.day == 24 and close.hour == 23
