from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

TEST_SECRET = "test-secret-key-for-testing-purposes-only-xyz123"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(cfg.settings, "APP_ENV", "test")


@pytest.fixture(autouse=True)
def reset_courier_caches():
    """
    Clear the three process-level courier caches between tests.

    `lalamove_service` and `slider_service` cache quotes, and
    `noon_send_service` caches the partner limits for the life of the process
    — deliberately, since they are asked once per boot in production. In a test
    run "the process" is the whole suite, so one test that populates a cache
    hands it to every test that follows, and the leak surfaces as an order that
    depends on test ordering.

    Individual tests already called `clear_caches()` by hand. That works only
    for the tests that remember, which is the shape of every convention this
    repo has since turned into a fixture or a guard test.
    """
    from app.services.couriers import (
        lalamove_service,
        noon_send_service,
        slider_service,
    )

    for reset in (
        lalamove_service.clear_caches,
        slider_service.clear_caches,
        noon_send_service.invalidate_limits,
    ):
        reset()
    yield
    for reset in (
        lalamove_service.clear_caches,
        slider_service.clear_caches,
        noon_send_service.invalidate_limits,
    ):
        reset()


@pytest.fixture
def mock_db():
    session = AsyncMock()
    session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    # Both accessors answer the same, so a test stubbing one is not silently
    # pinned to the SQLAlchemy call the code happens to use today.
    mock_result.scalars.return_value.first.return_value = None
    mock_result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=mock_result)
    return session


@pytest.fixture
async def client(mock_db):
    from app.core.deps import get_db
    from app.main import app

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
