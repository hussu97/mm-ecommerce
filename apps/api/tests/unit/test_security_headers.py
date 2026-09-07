"""
Security headers ride on the app, not only in nginx's TLS blocks (F-OPS-26).

They were `add_header` directives in the TLS server blocks, so the HTTP-only
nginx path and any response that did not traverse a TLS block carried none of
them. The app's `add_security_headers` middleware now sets the full set, so every
response from either app carries them regardless of how it was routed.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


async def _get(app, path="/ping"):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as ac:
        return await ac.get(path)


async def test_storefront_response_carries_the_baseline_headers():
    from app.main import app

    resp = await _get(app)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


async def test_register_response_carries_the_baseline_headers():
    from app.pos_main import app

    resp = await _get(app)
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"


async def test_hsts_is_off_outside_production():
    # conftest pins APP_ENV=test; HSTS is an HTTPS-only instruction.
    from app.main import app

    resp = await _get(app)
    assert "Strict-Transport-Security" not in resp.headers


async def test_hsts_is_set_in_production(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "APP_ENV", "production")
    from app.main import app

    resp = await _get(app)
    assert "max-age=" in resp.headers.get("Strict-Transport-Security", "")
    assert "includeSubDomains" in resp.headers["Strict-Transport-Security"]
