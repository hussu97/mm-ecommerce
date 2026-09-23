"""
The console's half of per-branch modifier-option stock.

The option-level twin of `test_admin_branch_availability`. A filling is 86'd per
branch, and that is what takes a box off one emirate's website — so the console
needs to set it, the stock half must go through `availability_service` (which
owns the clock and the put-back-clears-the-countdown rule), and the console's own
permission has to open it or an administrator can read the state and not change
it. The estate-wide read must also outrank `/{slug}`, or it 404s and the grid
never draws.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import products as products_api
from app.core.exceptions import BadRequestError, ForbiddenError
from app.services.catalog import availability_service


def _user(*permissions: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        can=lambda name: name in permissions,
        is_admin=False,
        is_staff=True,
    )


class _Db:
    def __init__(self):
        self.branch = SimpleNamespace(
            id=uuid.uuid4(), reference="K001", deleted_at=None, is_active=True
        )
        self.option = SimpleNamespace(id=uuid.uuid4(), name="Pistachio")

    async def get(self, model, _pk):
        return self.branch if model.__name__ == "Branch" else self.option

    async def refresh(self, _row):
        return None


async def _call(monkeypatch, user, **payload):
    db = _Db()
    stamped: dict = {}

    async def set_option_stock(
        _db, *, branch, option_id, in_stock, duration, actor, **_kw
    ):
        stamped.update(
            branch=branch, option_id=option_id, in_stock=in_stock, duration=duration
        )
        return SimpleNamespace(
            id=uuid.uuid4(),
            branch_id=branch.id,
            modifier_option_id=option_id,
            is_in_stock=in_stock,
            out_of_stock_until=None,
        )

    monkeypatch.setattr(availability_service, "set_option_stock", set_option_stock)
    monkeypatch.setattr(products_api.catalogue_cache, "retire", AsyncMock())
    monkeypatch.setattr(products_api.audit_service, "log_action", AsyncMock())
    monkeypatch.setattr(
        products_api.grubops_service, "push_change_in_background", lambda **_: None
    )

    request = products_api.SetOptionAvailabilityRequest(
        branch_id=db.branch.id, **payload
    )
    response = await products_api.set_modifier_option_availability(
        option_id=db.option.id,
        data=request,
        request=MagicMock(),
        db=db,
        user=user,
    )
    return response, stamped


async def test_a_console_permission_opens_it(monkeypatch):
    _, stamped = await _call(monkeypatch, _user("catalogue.manage"), is_in_stock=False)
    assert stamped["in_stock"] is False


async def test_the_register_permission_still_opens_it(monkeypatch):
    _, stamped = await _call(
        monkeypatch, _user("pos.products.availability"), is_in_stock=False
    )
    assert stamped["in_stock"] is False


async def test_neither_permission_is_refused(monkeypatch):
    with pytest.raises(ForbiddenError):
        await _call(monkeypatch, _user("orders.read"), is_in_stock=False)


async def test_the_stock_half_goes_through_the_service(monkeypatch):
    _, stamped = await _call(
        monkeypatch,
        _user("catalogue.manage"),
        is_in_stock=False,
        duration="end_of_day",
    )
    assert stamped["duration"] == "end_of_day"
    assert stamped["branch"].reference == "K001"


async def test_a_duration_the_register_cannot_produce_is_refused(monkeypatch):
    with pytest.raises(BadRequestError):
        await _call(
            monkeypatch,
            _user("catalogue.manage"),
            is_in_stock=False,
            duration="until_christmas",
        )


def test_the_estate_wide_read_is_not_shadowed_by_the_slug_route():
    """`/products/modifier-availability` must be declared before `/products/{slug}`."""
    from app.api.v1.products import router

    paths = [route.path for route in router.routes]

    assert paths.index("/modifier-availability") < paths.index("/{slug}"), (
        "/products/modifier-availability is shadowed by /products/{slug} and will 404"
    )
