"""
The Slider webhook endpoint.

`/webhooks/slider` deduplicates, applies the status and can move an order to
`delivered`. Slider does not sign requests — what they send is a **static token
in a header we choose the name of** — so that token is the entire boundary
between a genuine status update and anybody who guesses the URL, and unlike a
signature there is nothing behind it. It is therefore **enforced**, which is the
deliberate difference from the noon Send routes next door, where
`NOON_SEND_ENFORCE_WEBHOOK_KEY` is false in production because enforcing it once
dropped every status update for a live trial.

It must answer 200 to everything. A webhook URL that fails is retried and then
disabled, after which every later order silently loses its status; swallowing
one malformed push is the cheaper mistake.

The inert `/webhooks/slider/staging` route — which acknowledged Slider's sandbox
pushes and wrote nothing — was removed when the pilot moved to production.
"""

from __future__ import annotations

import pytest

TOKEN = "slider-production-token"
HEADER = "X-Slider-Token"

URL = "/api/v1/webhooks/slider"

#: Their status push, in the shape their reference documents. Note which way
#: round the two identifiers go: `order_number` is the handle **Slider**
#: allocates, `order_id` is the reference **we** sent on the create. This
#: fixture had them swapped, and with them swapped the reference lookup
#: searched our `courier_reference` column for Slider's number.
PUSH = {
    "order_number": 4820193,
    "order_id": "MM-1001",
    "status": "delivered",
    "estimated_delivery_time": None,
    "tracking_link": "https://track.slider.test/4820193",
    "driver_info": {
        "name": "Imran",
        "phone_number": "+971509876543",
        "latitude": 25.3463,
        "longitude": 55.4209,
        "vehicle": "Honda PCX",
    },
    "timestamp": "2026-08-21T10:14:00Z",
}


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_HEADER", HEADER)


@pytest.fixture(autouse=True)
def journal(monkeypatch):
    """`webhook_logs` rows, collected instead of written."""
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
        "app.services.webhook_log_service.AsyncSessionFactory", lambda: _Session()
    )
    return rows


class TestTheTokenIsEnforced:
    """
    Not merely recorded. The noon Send endpoints record and accept, because
    noon's staging side sends a key no screen of ours produced and refusing on a
    mismatch cost a live trial its first two status updates. Slider's token is
    one we set on both sides ourselves, so there is no such asymmetry to be
    caught by — and an unenforced token on an endpoint that can mark an order
    delivered is an open endpoint.
    """

    async def test_a_push_with_the_right_token_is_acted_on(self, client):
        response = await client.post(URL, json=PUSH, headers={HEADER: TOKEN})
        assert response.status_code == 200
        assert response.json().get("error") is None

    async def test_a_push_with_no_token_is_refused(self, client):
        response = await client.post(URL, json=PUSH)
        assert response.status_code == 200
        assert response.json()["error"] == "unauthorised"

    async def test_a_push_with_the_wrong_token_is_refused(self, client):
        response = await client.post(URL, json=PUSH, headers={HEADER: "guessed"})
        assert response.status_code == 200
        assert response.json()["error"] == "unauthorised"

    async def test_a_token_in_the_wrong_header_is_refused(self, client):
        """
        Slider's dashboard has a "Token Header Key" field and ours shipped
        **empty**. A token with no header name may not be sent at all, or may
        arrive somewhere nobody is reading — so the name is configuration, read
        at request time, and a token in any other header is no token.
        """
        response = await client.post(URL, json=PUSH, headers={"X-Api-Key": TOKEN})
        assert response.json()["error"] == "unauthorised"

    async def test_the_header_name_is_configurable(self, client, monkeypatch):
        import app.core.config as cfg

        monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_HEADER", "X-Their-Name")
        assert (
            await client.post(URL, json=PUSH, headers={"X-Their-Name": TOKEN})
        ).json().get("error") is None
        assert (await client.post(URL, json=PUSH, headers={HEADER: TOKEN})).json()[
            "error"
        ] == "unauthorised"

    async def test_no_configured_token_refuses_everything(self, client, monkeypatch):
        """
        Fails closed, and deliberately the opposite of the noon Send decision.
        There the risk was dropping real deliveries from a courier already
        carrying orders; here it is a brand-new endpoint with nothing riding on
        it. A pilot that fails closed is a pilot somebody fixes.
        """
        import app.core.config as cfg

        monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_TOKEN", "")
        response = await client.post(URL, json=PUSH, headers={HEADER: ""})
        assert response.json()["error"] == "unauthorised"

    async def test_the_token_is_never_logged_in_full(self, client, journal):
        await client.post(URL, json=PUSH, headers={HEADER: TOKEN})
        (row,) = journal
        assert TOKEN not in (row.api_key_fingerprint or "")
        assert row.api_key_fingerprint.startswith("slid")


class TestNeverFailing:
    async def test_a_push_for_a_delivery_we_do_not_hold_is_acknowledged(self, client):
        """Acknowledged and left alone, rather than retried at us for a day."""
        response = await client.post(URL, json=PUSH, headers={HEADER: TOKEN})
        assert response.status_code == 200
        assert response.json()["received"] is True
        assert response.json().get("matched") is not True

    async def test_a_push_naming_no_delivery_is_still_answered(self, client):
        response = await client.post(
            URL, json={"status": "delivered"}, headers={HEADER: TOKEN}
        )
        assert response.status_code == 200
        assert response.json()["received"] is True

    async def test_an_unparseable_body_is_still_answered(self, client):
        response = await client.post(
            URL,
            content=b"not json",
            headers={HEADER: TOKEN, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
        assert response.json()["received"] is True

    async def test_an_empty_body_is_still_answered(self, client):
        response = await client.post(URL, content=b"", headers={HEADER: TOKEN})
        assert response.status_code == 200


class TestTheRequestIsWrittenDown:
    async def test_a_push_is_journalled_with_its_payload(self, client, journal):
        await client.post(URL, json=PUSH, headers={HEADER: TOKEN})

        (row,) = journal
        assert row.provider == "slider"
        assert row.endpoint == "status"
        assert row.event_type == "delivered"
        assert row.external_id == "4820193"
        assert row.order_number == "MM-1001"
        assert row.payload == PUSH
        assert row.signature_valid is True

    async def test_a_refused_push_is_journalled_too(self, client, journal):
        """The one you most want to see afterwards."""
        await client.post(URL, json=PUSH, headers={HEADER: "guessed"})

        (row,) = journal
        assert row.signature_valid is False
        assert row.payload == PUSH
