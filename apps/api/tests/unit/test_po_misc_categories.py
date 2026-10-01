"""Misc PO line categories and periods: preset ranges, admin-only visibility,
the P&L's per-day proration, and the routes both apps mount."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.exceptions import BadRequestError
from app.services.inventory import po_misc_service
from app.services.orders.misc_expenses import prorate

# ─── default_range ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("unit", "length", "today", "expected"),
    [
        ("day", 1, date(2026, 9, 26), (date(2026, 9, 26), date(2026, 9, 26))),
        ("day", 3, date(2026, 9, 30), (date(2026, 9, 30), date(2026, 10, 2))),
        # 2026-09-26 is a Saturday; the week starts Monday the 21st.
        ("week", 1, date(2026, 9, 26), (date(2026, 9, 21), date(2026, 9, 27))),
        # A Sunday stays in the week that began the Monday before.
        ("week", 1, date(2026, 9, 27), (date(2026, 9, 21), date(2026, 9, 27))),
        ("week", 2, date(2026, 9, 21), (date(2026, 9, 21), date(2026, 10, 4))),
        ("month", 1, date(2026, 9, 26), (date(2026, 9, 1), date(2026, 9, 30))),
        ("month", 1, date(2028, 2, 10), (date(2028, 2, 1), date(2028, 2, 29))),
        # Quarters and years are calendar blocks counted from January.
        ("month", 3, date(2026, 9, 26), (date(2026, 7, 1), date(2026, 9, 30))),
        ("month", 3, date(2026, 11, 2), (date(2026, 10, 1), date(2026, 12, 31))),
        ("month", 6, date(2026, 9, 26), (date(2026, 7, 1), date(2026, 12, 31))),
        ("month", 12, date(2026, 9, 26), (date(2026, 1, 1), date(2026, 12, 31))),
        # A length that does not divide the year starts this month.
        ("month", 5, date(2026, 9, 26), (date(2026, 9, 1), date(2027, 1, 31))),
        ("month", 24, date(2026, 9, 26), (date(2026, 9, 1), date(2028, 8, 31))),
    ],
)
def test_default_range(unit, length, today, expected):
    assert po_misc_service.default_range(unit, length, today) == expected


def test_default_range_refuses_an_unknown_unit():
    with pytest.raises(BadRequestError):
        po_misc_service.default_range("fortnight", 1, date(2026, 9, 26))


# ─── proration ────────────────────────────────────────────────────────────────


def test_a_year_of_rent_spreads_per_day():
    year = (date(2026, 1, 1), date(2026, 12, 31))
    sept = prorate(Decimal("12000"), *year, date(2026, 9, 1), date(2026, 9, 30))
    assert round(sept, 2) == Decimal("986.30")
    one_day = prorate(Decimal("12000"), *year, date(2026, 9, 26), date(2026, 9, 26))
    assert round(one_day, 2) == Decimal("32.88")


def test_proration_counts_only_the_overlap():
    # A month's line seen through a window that starts mid-month.
    amount = prorate(
        Decimal("300"),
        date(2026, 9, 1),
        date(2026, 9, 30),
        date(2026, 9, 21),
        date(2026, 10, 15),
    )
    assert amount == Decimal("100")


def test_proration_of_a_whole_period_is_the_whole_amount():
    assert prorate(
        Decimal("147"),
        date(2026, 9, 1),
        date(2026, 9, 30),
        date(2026, 8, 1),
        date(2026, 12, 31),
    ) == Decimal("147")


def test_proration_outside_the_window_is_zero():
    assert prorate(
        Decimal("500"),
        date(2026, 9, 1),
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 31),
    ) == Decimal("0")


# ─── visibility ───────────────────────────────────────────────────────────────


def _user(*, admin=False, slugs=()):
    role = SimpleNamespace(has=lambda p: p in slugs)
    return SimpleNamespace(
        is_admin=admin,
        role=role,
        can=lambda p: admin or p in slugs,
    )


def test_only_the_restricted_permission_sees_admin_only_categories():
    holder = _user(slugs={po_misc_service.RESTRICTED_MISC_PERMISSION})
    cashier = _user(slugs={"inventory.purchase_orders.manage"})
    assert po_misc_service.can_see_gated(holder)
    assert po_misc_service.can_see_gated(_user(admin=True))
    assert not po_misc_service.can_see_gated(cashier)


def test_the_till_never_sees_admin_only_categories_even_for_an_admin():
    assert not po_misc_service.can_see_gated(_user(admin=True), pos=True)


def _line(net, vat, gross, *, admin_only):
    return SimpleNamespace(
        id=uuid.uuid4(),
        net_total=Decimal(net),
        vat_amount=Decimal(vat),
        entered_total=Decimal(gross),
        category_admin_only=admin_only,
    )


def _po(*, items=(), misc=(), additional="0"):
    lines = list(misc)
    net = sum((m.net_total for m in lines), Decimal("0")) + sum(
        (i.net_total for i in items), Decimal("0")
    )
    vat = sum((m.vat_amount for m in lines), Decimal("0")) + sum(
        (i.vat_amount for i in items), Decimal("0")
    )
    gross = sum((m.entered_total for m in lines), Decimal("0")) + sum(
        (i.entered_total for i in items), Decimal("0")
    )
    return SimpleNamespace(
        items=list(items),
        misc_items=lines,
        subtotal_net=net,
        vat_total=vat,
        total_gross=gross,
        total_cost=gross + Decimal(additional),
    )


def test_hidden_lines_leave_totals_that_still_add_up():
    rent = _line("2000", "100", "2100", admin_only=True)
    soap = _line("20", "1", "21", admin_only=False)
    po = _po(misc=[rent, soap], additional="5")
    view = po_misc_service.visible_misc(po, sees_gated=False)
    assert view.misc_items == [soap]
    assert (view.subtotal_net, view.vat_total, view.total_gross, view.total_cost) == (
        Decimal("20.00"),
        Decimal("1.00"),
        Decimal("21.00"),
        Decimal("26.00"),
    )
    full = po_misc_service.visible_misc(po, sees_gated=True)
    assert full.misc_items == [rent, soap]
    assert full.total_gross == Decimal("2121.00")


def test_hide_gated_lines_rewrites_the_response():
    rent = _line("2000", "0", "2000", admin_only=True)
    soap = _line("20", "1", "21", admin_only=False)
    po = _po(misc=[rent, soap])
    payload = SimpleNamespace(
        misc_items=[SimpleNamespace(id=rent.id), SimpleNamespace(id=soap.id)],
        subtotal_net=po.subtotal_net,
        vat_total=po.vat_total,
        total_gross=po.total_gross,
        total_cost=po.total_cost,
    )
    po_misc_service.hide_gated_lines(payload, po)
    assert [m.id for m in payload.misc_items] == [soap.id]
    assert payload.total_gross == Decimal("21.00")


def test_a_po_of_only_admin_only_lines_does_not_exist_for_the_till():
    rent_po = _po(misc=[_line("2000", "0", "2000", admin_only=True)])
    mixed = _po(
        misc=[
            _line("2000", "0", "2000", admin_only=True),
            _line("20", "1", "21", admin_only=False),
        ]
    )
    stock = _po(
        items=[SimpleNamespace(net_total=Decimal("1"), vat_amount=0, entered_total=1)],
        misc=[_line("2000", "0", "2000", admin_only=True)],
    )
    assert not po_misc_service.po_is_visible(rent_po, sees_gated=False)
    assert po_misc_service.po_is_visible(rent_po, sees_gated=True)
    assert po_misc_service.po_is_visible(mixed, sees_gated=False)
    assert po_misc_service.po_is_visible(stock, sees_gated=False)


# ─── Wiring ───────────────────────────────────────────────────────────────────


def _leaf_endpoints(routes):
    """Route endpoints in match order, through FastAPI's lazy included routers
    (the walk ``test_route_authenticators`` does)."""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield route.endpoint
        elif type(route).__name__ == "_IncludedRouter":
            yield from _leaf_endpoints(route.original_router.routes)
        elif getattr(route, "routes", None):
            yield from _leaf_endpoints(route.routes)


def test_the_till_serves_the_pickers_ahead_of_the_po_detail_route():
    """`/pos/purchase-orders/{po_id}` is UUID-typed, so a picker path matched
    after it would 422. Both pickers are on the POS app, and matched first."""
    from app.api.v1 import inventory, po_misc
    from app.pos_main import app as pos_app

    paths = pos_app.openapi()["paths"]
    assert "get" in paths["/api/v1/pos/purchase-orders/misc-categories"]
    assert "get" in paths["/api/v1/pos/purchase-orders/misc-periods"]
    order = list(_leaf_endpoints(pos_app.routes))
    detail = order.index(inventory.pos_get_purchase_order)
    assert order.index(po_misc.pos_list_misc_categories) < detail
    assert order.index(po_misc.pos_list_misc_periods) < detail


def test_the_console_mounts_category_and_period_crud():
    from app.main import app as web_app

    paths = web_app.openapi()["paths"]
    for resource in ("po-misc-categories", "po-misc-periods"):
        assert {"get", "post"} <= set(paths[f"/api/v1/inventory/{resource}"])
    assert (
        "patch"
        in paths["/api/v1/inventory/purchase-orders/{po_id}/misc-items/{line_id}"]
    )


def test_the_migration_seeds_the_owner_list_and_backfills_by_id():
    source = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/293_po_misc_categories.py"
    ).read_text()
    for name, gated in (
        ("Cake Supplies", False),
        ("Groceries", False),
        ("Rent", True),
        ("Utilities", False),
        ("Cleaning Supplies", False),
        ("Salary", True),
        ("Trade License", True),
        ("Misc. Government Expense", False),
        ("Health Insurance and Visa", True),
    ):
        assert f'("{name}", {gated})' in source
    assert '("This month", "month", 1, True, 3)' in source
    for line_id in (
        "8b598bf4-5dcf-4a2e-b305-8c0ec83f2040",
        "e23db1c1-9491-43bc-903a-7da55be6a49e",
        "c7e53f24-46cc-4dd1-88c0-6ebee27edd55",
        "54ef9a8d-2fe8-48fe-ab49-e3e3982f11ce",
    ):
        assert line_id in source
