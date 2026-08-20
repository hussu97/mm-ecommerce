"""
The console's half of per-branch stock.

The register has been able to mark a product out at its own branch since the
"86 it" button existed. The console could not — it could not even see the state,
which since the storefront started answering per branch means an administrator
looking at a cake missing from one emirate's website had no screen that said
why.

Two things this guards. The stock half must go through `availability_service`,
which owns what `end_of_day` means and the rule that putting something back
clears the countdown as well as the flag — a second implementation here would
drift from the register's. And the console's own permission has to open it, or
an administrator can read the state and not change it.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import products as products_api
from app.core.exceptions import BadRequestError, ForbiddenError
from app.services import availability_service


def _user(*permissions: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        can=lambda name: name in permissions,
        is_admin=False,
        is_staff=True,
    )


class _Db:
    """A branch, a product, and a row that does not exist yet."""

    def __init__(self):
        self.branch = SimpleNamespace(
            id=uuid.uuid4(), reference="K001", deleted_at=None, is_active=True
        )
        self.product = SimpleNamespace(id=uuid.uuid4(), name="Basque Cheesecake")

    async def get(self, model, _pk):
        return self.branch if model.__name__ == "Branch" else self.product

    async def execute(self, _statement):
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        return result

    async def flush(self):
        return None

    async def refresh(self, row):
        # What Postgres does on the way back: a row built in memory reads None
        # for every server-side default until it has been through the database.
        # Without this the price-only case fails validation on columns nobody
        # set, which is a fake's artefact rather than anything the endpoint does.
        if row.is_in_stock is None:
            row.is_in_stock = True
        if row.is_active is None:
            row.is_active = True

    def add(self, _row):
        return None


async def _call(monkeypatch, user, **payload):
    db = _Db()
    stamped: dict = {}

    async def set_product_stock(_db, *, branch, product_id, in_stock, duration):
        stamped.update(branch=branch, in_stock=in_stock, duration=duration)
        return SimpleNamespace(
            id=uuid.uuid4(),
            branch_id=branch.id,
            product_id=product_id,
            is_in_stock=in_stock,
            is_active=True,
            price=None,
            out_of_stock_until=None,
        )

    monkeypatch.setattr(availability_service, "set_product_stock", set_product_stock)
    monkeypatch.setattr(products_api.catalogue_cache, "retire", AsyncMock())
    monkeypatch.setattr(products_api.audit_service, "log_action", AsyncMock())

    request = products_api.SetAvailabilityRequest(branch_id=db.branch.id, **payload)
    response = await products_api.set_branch_availability(
        product_id=db.product.id,
        data=request,
        request=MagicMock(),
        db=db,
        user=user,
    )
    return response, stamped


async def test_a_console_permission_opens_it(monkeypatch):
    """
    `catalogue.manage` is the console's authority over the catalogue, and this
    is a column of it. Requiring the register's permission instead left an
    administrator able to read the state and not change it.
    """
    _, stamped = await _call(monkeypatch, _user("catalogue.manage"), is_in_stock=False)

    assert stamped["in_stock"] is False


async def test_the_register_permission_still_opens_it(monkeypatch):
    """Two ways of describing one authority over one column."""
    _, stamped = await _call(
        monkeypatch, _user("pos.products.availability"), is_in_stock=False
    )

    assert stamped["in_stock"] is False


async def test_neither_permission_is_refused(monkeypatch):
    with pytest.raises(ForbiddenError):
        await _call(monkeypatch, _user("orders.read"), is_in_stock=False)


async def test_the_stock_half_goes_through_the_service(monkeypatch):
    """
    Not through the row. `set_product_stock` owns the clock — what `end_of_day`
    means for a shop trading past midnight, and the CHECK that refuses a row
    which is available and still counting down.
    """
    _, stamped = await _call(
        monkeypatch,
        _user("catalogue.manage"),
        is_in_stock=False,
        duration="end_of_day",
    )

    assert stamped["duration"] == "end_of_day"
    assert stamped["branch"].reference == "K001"


async def test_a_duration_the_register_cannot_produce_is_refused(monkeypatch):
    """
    The console must not invent a fourth answer. Three is what the terminal
    offers, what `resolve_until` knows how to date, and what the shop says out
    loud.
    """
    with pytest.raises(BadRequestError):
        await _call(
            monkeypatch,
            _user("catalogue.manage"),
            is_in_stock=False,
            duration="until_christmas",
        )


async def test_a_price_only_change_touches_no_clock(monkeypatch):
    """
    Editing a branch price is not a stockout. Routing it through
    `set_product_stock` would stamp `is_in_stock` from a field nobody sent.
    """
    _, stamped = await _call(monkeypatch, _user("catalogue.manage"), price=99)

    assert stamped == {}


# ── reachability ──────────────────────────────────────────────────────────────


def test_the_estate_wide_read_is_not_shadowed_by_the_slug_route():
    """
    `/products/availability` has to be declared before `/products/{slug}`.

    FastAPI matches in declaration order. Written beside the rest of the
    availability section — which sits below `/{slug}` — this route was read as a
    product whose slug is "availability" and answered 404 to every caller, while
    `/availability/{branch_id}` kept working because two segments cannot match a
    one-segment path. One of them worked, so nothing looked broken; the console
    simply drew no column.

    Asserted on the router's own ordering rather than through a client, because
    a client test would need the auth this route requires and would then be
    testing the dependency instead of the order.
    """
    from app.api.v1.products import router

    paths = [route.path for route in router.routes]

    assert paths.index("/availability") < paths.index("/{slug}"), (
        "/products/availability is shadowed by /products/{slug} and will 404"
    )
