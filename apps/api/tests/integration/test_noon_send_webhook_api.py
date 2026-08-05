"""
The noon Send webhook endpoints.

Two things distinguish these from the Lalamove one and both need pinning.

They do not sign requests. The only credential is a shared key in `X-API-Key`,
so that header is the entire boundary between a genuine status update and
anybody who guesses the URL — and unlike a signature there is no second check
behind it.

They also must never answer anything but 200. A webhook URL that fails is
retried and then disabled, after which every later order silently loses its
status. Swallowing one malformed push is the cheaper mistake, so even a rejected
one comes back 200 with the refusal in the body.
"""

from __future__ import annotations

import pytest

KEY = "webhook-shared-secret"
STATUS_URL = "/api/v1/webhooks/noon-send"
TRACKING_URL = "/api/v1/webhooks/noon-send/tracking"

PUSH = {
    "order_nr": "EHG84NNJMVG35BTDE",
    "status_code": "picked_up",
    "order_reference": "MM-1001",
    "timestamp": "2026-08-04 08:55:14",
}


@pytest.fixture(autouse=True)
def webhook_key(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "NOON_SEND_WEBHOOK_API_KEY", KEY)


class TestAuthentication:
    """
    noon Send's staging environment sends no `X-API-Key` — there is nowhere in
    it to configure one — so demanding a key dropped every status update during
    the trial, which is the thing the trial exists to exercise.

    A keyless push is therefore accepted. A push carrying the *wrong* key is
    still refused, which catches the realistic mistake: their production side
    configured with a stale one.
    """

    async def test_a_push_without_a_key_is_accepted(self, client):
        response = await client.post(STATUS_URL, json=PUSH)
        assert response.status_code == 200
        assert "error" not in response.json()

    async def test_a_push_with_the_wrong_key_changes_nothing(self, client):
        response = await client.post(
            STATUS_URL, json=PUSH, headers={"X-API-Key": "not-it"}
        )
        assert response.json()["error"] == "unauthorised"

    async def test_the_right_key_is_accepted(self, client):
        response = await client.post(STATUS_URL, json=PUSH, headers={"X-API-Key": KEY})
        assert "error" not in response.json()

    async def test_an_unconfigured_deployment_accepts_a_key_it_cannot_check(
        self, client, monkeypatch
    ):
        """With no key configured there is nothing to compare against."""
        import app.core.config as cfg

        monkeypatch.setattr(cfg.settings, "NOON_SEND_WEBHOOK_API_KEY", "")
        response = await client.post(
            STATUS_URL, json=PUSH, headers={"X-API-Key": "anything"}
        )
        assert "error" not in response.json()

    async def test_the_tracking_endpoint_follows_the_same_rule(self, client):
        body = {"order_nr": "X", "da_details": {"latitude": 25.3, "longitude": 55.3}}
        assert "error" not in (await client.post(TRACKING_URL, json=body)).json()

        wrong = await client.post(
            TRACKING_URL, json=body, headers={"X-API-Key": "not-it"}
        )
        assert wrong.json()["error"] == "unauthorised"


class TestTheTaskNumberIsTheRemainingGuard:
    """
    What is left protecting these endpoints once a keyless push is accepted.

    Acting on a push requires naming a task we already dispatched — sixteen
    characters we never publish. Anything else is acknowledged and ignored, so
    a stranger who finds the URL can make no order move.
    """

    async def test_a_push_for_a_task_we_do_not_hold_moves_nothing(self, client):
        response = await client.post(STATUS_URL, json=PUSH)
        assert response.status_code == 200
        assert response.json().get("matched") is not True


class TestNeverFailing:
    async def test_an_authenticated_push_for_an_unknown_task_is_accepted(self, client):
        """
        A task we have no record of is acknowledged and left alone, rather than
        retried at us for a day.
        """
        response = await client.post(STATUS_URL, json=PUSH, headers={"X-API-Key": KEY})
        assert response.status_code == 200
        assert response.json()["received"] is True

    async def test_a_push_naming_no_task_is_still_answered(self, client):
        response = await client.post(
            STATUS_URL, json={"status_code": "picked_up"}, headers={"X-API-Key": KEY}
        )
        assert response.status_code == 200
        assert response.json()["received"] is True

    async def test_an_unparseable_body_is_still_answered(self, client):
        response = await client.post(
            STATUS_URL,
            content=b"not json",
            headers={"X-API-Key": KEY, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["received"] is True

    async def test_an_empty_body_is_still_answered(self, client):
        response = await client.post(
            STATUS_URL, content=b"", headers={"X-API-Key": KEY}
        )
        assert response.status_code == 200

    async def test_a_tracking_push_for_an_unknown_task_is_accepted(self, client):
        response = await client.post(
            TRACKING_URL,
            json={
                "order_nr": "EHG84NNJMVG35BTDE",
                "order_reference": "MM-1001",
                "da_details": {"latitude": 25.33, "longitude": 55.37},
                "timestamp": "2026-08-04 08:55:14",
            },
            headers={"X-API-Key": KEY},
        )
        assert response.status_code == 200
        assert response.json() == {"received": True, "matched": False}
