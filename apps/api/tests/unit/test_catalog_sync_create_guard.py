"""The `create_menu_item` idempotency guard (F-AGG-14).

A real create refuses when the product is already on the target — otherwise a
double-click created it twice on Foodics and cascaded the duplicate to every
marketplace. `force` overrides. No DB: the product load and the duplicate lookup
are faked, so only the guard wiring is under test.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import BadRequestError, ConflictError
from app.services.aggregators import catalog_sync


class _Db:
    def __init__(self, product):
        self._product = product

    async def execute(self, _stmt):
        return SimpleNamespace(scalar_one_or_none=lambda: self._product)


@pytest.fixture
def _writes_on(monkeypatch):
    monkeypatch.setattr(catalog_sync, "_ensure_write_enabled", lambda: None)


@pytest.mark.asyncio
async def test_a_real_create_refuses_a_duplicate(monkeypatch, _writes_on):
    product = SimpleNamespace(id=uuid4(), name="Basque Cheesecake", category=None)

    async def already(db, *, system, product_id):
        return SimpleNamespace(external_ref="foodics-123")

    monkeypatch.setattr(catalog_sync, "_already_created_on", already)

    with pytest.raises(ConflictError, match="already on"):
        await catalog_sync.create_menu_item(
            _Db(product), product_id=product.id, dry_run=False
        )


@pytest.mark.asyncio
async def test_force_skips_the_duplicate_guard(monkeypatch, _writes_on):
    product = SimpleNamespace(id=uuid4(), name="Basque Cheesecake", category=None)

    async def must_not_check(db, *, system, product_id):
        raise AssertionError("force must not consult the duplicate guard")

    monkeypatch.setattr(catalog_sync, "_already_created_on", must_not_check)

    # target=keeta hits the worker-only refusal right after the (skipped) guard,
    # which proves the guard was bypassed without needing the full create path.
    with pytest.raises(BadRequestError, match="no server-callable menu API"):
        await catalog_sync.create_menu_item(
            _Db(product),
            product_id=product.id,
            target="keeta",
            dry_run=False,
            force=True,
        )


@pytest.mark.asyncio
async def test_a_dry_run_never_consults_the_guard(monkeypatch, _writes_on):
    product = SimpleNamespace(id=uuid4(), name="Basque Cheesecake", category=None)

    async def must_not_check(db, *, system, product_id):
        raise AssertionError("a dry run creates nothing, so it does not guard")

    monkeypatch.setattr(catalog_sync, "_already_created_on", must_not_check)

    # keeta is worker-only, so a dry run still reaches that refusal — but never the
    # guard, which is the point.
    with pytest.raises(BadRequestError, match="no server-callable menu API"):
        await catalog_sync.create_menu_item(
            _Db(product), product_id=product.id, target="keeta", dry_run=True
        )
