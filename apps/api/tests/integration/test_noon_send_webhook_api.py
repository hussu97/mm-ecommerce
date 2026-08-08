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
    The key is recorded and not enforced.

    noon Send does not sign requests, and the key their staging side sends is
    not the one we configured — it is a value no screen of ours produced.
    Refusing on a mismatch dropped every status update for the trial:
    `assigned` and `picked_up` both arrived, two seconds after noon's own
    records show them, and both were thrown away with a warning nobody was
    watching. Two hours were spent concluding they had never been sent.

    So every push is acted on, and every push is logged with a fingerprint of
    the key it carried next to a fingerprint of ours. When those agree, this can
    go back to comparing them.
    """

    async def test_a_push_without_a_key_is_accepted(self, client):
        response = await client.post(STATUS_URL, json=PUSH)
        assert response.status_code == 200
        assert "error" not in response.json()

    async def test_a_push_with_an_unrecognised_key_is_still_acted_on(self, client):
        """The case that cost us the trial's first two status updates."""
        response = await client.post(
            STATUS_URL, json=PUSH, headers={"X-API-Key": "noons-own-key"}
        )
        assert response.status_code == 200
        assert "error" not in response.json()

    async def test_the_tracking_endpoint_follows_the_same_rule(self, client):
        body = {"order_nr": "X", "da_details": {"location": {"latitude": "252017557"}}}
        for headers in ({}, {"X-API-Key": "noons-own-key"}):
            response = await client.post(TRACKING_URL, json=body, headers=headers)
            assert response.status_code == 200
            assert "error" not in response.json()

    async def test_the_key_is_never_logged_in_full(self):
        """
        A fingerprint identifies which key is in play across two systems. The
        key itself in a log is a credential in a log.
        """
        from app.api.v1.webhooks import _key_fingerprint

        secret = "SstJi9Ho0EHG2t7kQVSz7nA2hOeL3iiwVxHxb0Njk60Q"
        printed = _key_fingerprint(secret)

        assert secret not in printed
        assert printed.startswith("SstJ")
        assert printed.endswith("(len=44)")
        assert _key_fingerprint(None) == "(none)"
        # Too short to fingerprint without giving it away.
        assert _key_fingerprint("abc123") == "len=6"


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


class TestTheRequestIsWrittenDown:
    """
    Every push lands in `webhook_logs`, whatever became of it.

    This is the table that did not exist on 2026-08-05, when MM-20260805-007
    received four pushes and -006 received one, and the only evidence either way
    was container stdout that a restart had already taken. noon Send do not
    retry and keep nothing to replay from, so a push nobody recorded is a push
    that never happened.
    """

    @pytest.fixture
    def recorded(self, monkeypatch):
        rows = []

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            def add(self, row):
                rows.append(row)

            async def commit(self):
                return None

        monkeypatch.setattr(
            "app.services.webhook_log_service.AsyncSessionFactory",
            lambda: _Session(),
        )
        return rows

    async def test_a_status_push_is_recorded_with_its_payload(self, client, recorded):
        await client.post(STATUS_URL, json=PUSH, headers={"X-API-Key": KEY})

        (row,) = recorded
        assert row.provider == "noon_send"
        assert row.endpoint == "status"
        assert row.event_type == "picked_up"
        assert row.external_id == "EHG84NNJMVG35BTDE"
        assert row.order_number == "MM-1001"
        assert row.payload == PUSH
        # The key we were sent, recognisably but not usably.
        assert row.api_key_fingerprint
        assert KEY not in row.api_key_fingerprint

    async def test_a_tracking_ping_is_recorded_too(self, client, recorded):
        """
        At full payload, and every one of them. They arrive every 15-30 seconds
        per live task, which is affordable only because the table is purged on a
        retention window — and worth it because "thirteen pings arrived and moved
        nothing" is a bug this endpoint has already had once, invisibly.
        """
        ping = {
            "order_nr": "EHG84NNJMVG35BTDE",
            "da_details": {"latitude": "251999780", "longitude": "552727353"},
        }
        await client.post(TRACKING_URL, json=ping, headers={"X-API-Key": KEY})

        (row,) = recorded
        assert row.endpoint == "tracking"
        assert row.event_type == "tracking"
        assert row.payload == ping

    async def test_a_push_with_no_readable_body_is_recorded(self, client, recorded):
        """The ones that fail are the ones somebody will come looking for."""
        await client.post(
            STATUS_URL,
            content=b"{not json",
            headers={"Content-Type": "application/json"},
        )

        (row,) = recorded
        assert row.error == "unreadable body"

    async def test_a_push_that_matched_no_order_says_so(self, client, recorded):
        """A run of these is either their bug or ours, and it used to be silent."""
        await client.post(STATUS_URL, json=PUSH, headers={"X-API-Key": KEY})

        (row,) = recorded
        assert row.matched is False
