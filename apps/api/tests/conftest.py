from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock

TEST_SECRET = "test-secret-key-for-testing-purposes-only-xyz123"


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(cfg.settings, "APP_ENV", "test")


@pytest.fixture
def mock_db():
    session = AsyncMock()
    session.add = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=mock_result)
    return session


@pytest.fixture
async def client(mock_db):
    from app.main import app
    from app.core.deps import get_db

    async def override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
