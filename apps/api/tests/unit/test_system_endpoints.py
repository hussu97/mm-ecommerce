"""
The three system endpoints, and which of them is allowed to fail.

`/ping` is what the container healthcheck polls, so it must depend on nothing:
anything it touches can restart the container by being slow. `/health` adds the
database, and is what the post-deploy smoke test reads. `/health/integrations`
reaches third parties and is therefore deliberately neither of those — a probe
that lets Google's uptime decide ours turns their outage into our restart loop.
"""

from __future__ import annotations

import pytest

from app.services import firebase_auth_service


@pytest.mark.asyncio
async def test_ping_depends_on_nothing(client):
    response = await client.get("/ping")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_integrations_reports_firebase_reachable(client, monkeypatch):
    monkeypatch.setattr(firebase_auth_service, "is_enabled", lambda: True)

    async def reachable() -> bool:
        return True

    monkeypatch.setattr(firebase_auth_service, "certificates_reachable", reachable)

    response = await client.get("/health/integrations")

    assert response.status_code == 200
    assert response.json()["checks"]["firebase_certificates"] == "ok"


@pytest.mark.asyncio
async def test_integrations_reports_an_unreachable_certificate_endpoint(
    client, monkeypatch
):
    """
    The case the probe was written for and never wired up to catch: phone
    verification fails closed, so a deploy that cannot reach Google's keys
    looks exactly like nobody trying to sign up.
    """
    monkeypatch.setattr(firebase_auth_service, "is_enabled", lambda: True)

    async def unreachable() -> bool:
        return False

    monkeypatch.setattr(firebase_auth_service, "certificates_reachable", unreachable)

    response = await client.get("/health/integrations")

    # Reported, not fatal: this endpoint states what it found, and nothing
    # restarts on the strength of it.
    assert response.status_code == 200
    assert response.json()["checks"]["firebase_certificates"] == "unreachable"


@pytest.mark.asyncio
async def test_integrations_says_disabled_rather_than_calling_out(client, monkeypatch):
    monkeypatch.setattr(firebase_auth_service, "is_enabled", lambda: False)

    def must_not_be_called() -> (
        bool
    ):  # pragma: no cover - the assertion is that it is not
        raise AssertionError("probed a third party for a feature that is switched off")

    monkeypatch.setattr(
        firebase_auth_service, "certificates_reachable", must_not_be_called
    )

    response = await client.get("/health/integrations")

    assert response.json()["checks"]["firebase_certificates"] == "disabled"
