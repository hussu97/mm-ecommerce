"""
The Slider webhook endpoints.

Two of them, and the difference between them is the whole point.

`/webhooks/slider` is the real one: it deduplicates, applies the status and can
move an order to `delivered`. Slider does not sign requests — what they send is
a **static token in a header we choose the name of** — so that token is the
entire boundary between a genuine status update and anybody who guesses the URL,
and unlike a signature there is nothing behind it. It is therefore **enforced**,
which is the deliberate difference from the noon Send routes next door, where
`NOON_SEND_ENFORCE_WEBHOOK_KEY` is false in production because enforcing it once
dropped every status update for a live trial.

`/webhooks/slider/staging` is pointed at production on purpose and does
**nothing**. Their dashboard configures the two environments separately, and
aiming the staging one here means real Slider traffic can be watched while the
integration is still being proved. A staging delivery id is meaningless against
production data, and a staging `delivered` that moved a real order would refund
a cake sitting on a shelf.

Both must answer 200 to everything. A webhook URL that fails is retried and then
disabled, after which every later order silently loses its status; swallowing
one malformed push is the cheaper mistake.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.order import Order, OrderStatusEnum
from app.models.order_delivery import OrderDelivery

TOKEN = "slider-production-token"
STAGING_TOKEN = "slider-staging-token"
HEADER = "X-Slider-Token"

URL = "/api/v1/webhooks/slider"
STAGING_URL = "/api/v1/webhooks/slider/staging"

PUSH = {
    "id": "SLD-4820193",
    "order_number": "MM-1001",
    "status": "delivered",
    "timestamp": "2026-08-21T10:14:00Z",
    "rider": {"id": "r_88", "name": "Imran", "phone": "+971509876543"},
}


@pytest.fixture(autouse=True)
def tokens(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_TOKEN", TOKEN)
    monkeypatch.setattr(cfg.settings, "SLIDER_WEBHOOK_HEADER", HEADER)
    monkeypatch.setattr(cfg.settings, "SLIDER_STAGING_WEBHOOK_TOKEN", STAGING_TOKEN)
    monkeypatch.setattr(cfg.settings, "SLIDER_STAGING_WEBHOOK_HEADER", HEADER)


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
        Slider's dashboard has a "Token Header Key" field per environment and
        both of ours shipped **empty**. A token with no header name may not be
        sent at all, or may arrive somewhere nobody is reading — so the name is
        configuration, read at request time, and a token in any other header is
        no token.
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
        assert row.external_id == "SLD-4820193"
        assert row.order_number == "MM-1001"
        assert row.payload == PUSH
        assert row.signature_valid is True

    async def test_a_refused_push_is_journalled_too(self, client, journal):
        """The one you most want to see afterwards."""
        await client.post(URL, json=PUSH, headers={HEADER: "guessed"})

        (row,) = journal
        assert row.signature_valid is False
        assert row.payload == PUSH


# ── the staging endpoint ──────────────────────────────────────────────────────


def _snapshot(instance) -> dict:
    """Every mapped column's value, so "unchanged" can be asserted rather than
    hoped for."""
    return {
        column.key: getattr(instance, column.key)
        for column in instance.__table__.columns
    }


@pytest.fixture
def a_real_order(mock_db):
    """
    An order and its delivery row, reachable through the session the request is
    given — so a staging handler that *did* resolve an order would find one and
    a change would show.
    """
    order = Order(
        id=uuid.uuid4(),
        order_number="MM-1001",
        status=OrderStatusEnum.OUT_FOR_DELIVERY,
        total=Decimal("185.00"),
    )
    delivery = OrderDelivery(
        order_id=order.id,
        provider="slider",
        courier_order_id="SLD-4820193",
        courier_status="in_transit",
    )

    result = MagicMock()
    result.scalars.return_value.first.return_value = delivery
    result.scalar_one_or_none = MagicMock(return_value=delivery)
    mock_db.execute = AsyncMock(return_value=result)
    return order, delivery


class TestTheStagingEndpointWritesNothing:
    """
    A comment saying "staging does nothing" would not stop somebody wiring it up
    later. This is what does.
    """

    async def test_a_delivered_push_leaves_the_order_and_its_delivery_untouched(
        self, client, mock_db, a_real_order
    ):
        order, delivery = a_real_order
        before = (_snapshot(order), _snapshot(delivery))

        response = await client.post(
            STAGING_URL, json=PUSH, headers={HEADER: STAGING_TOKEN}
        )

        assert response.status_code == 200
        assert response.json() == {"received": True}
        assert (_snapshot(order), _snapshot(delivery)) == before

    async def test_it_never_reaches_the_database_at_all(
        self, client, mock_db, a_real_order
    ):
        """
        Stronger than comparing the two rows, and the assertion that would fail
        first: no `webhook_events` insert, no order lookup, no commit. Nothing
        that touches the request's session happens here.
        """
        await client.post(STAGING_URL, json=PUSH, headers={HEADER: STAGING_TOKEN})

        mock_db.execute.assert_not_awaited()
        mock_db.add.assert_not_called()
        mock_db.commit.assert_not_awaited()

    async def test_the_real_endpoint_does_reach_the_database(
        self, client, mock_db, a_real_order
    ):
        """
        The control. Without it the two assertions above would keep passing if
        the whole Slider integration were deleted.
        """
        await client.post(URL, json=PUSH, headers={HEADER: TOKEN})
        mock_db.execute.assert_awaited()

    async def test_it_has_its_own_token(self, client, a_real_order):
        """
        Their dashboard configures the two environments separately, so the
        production token must not open the staging route or vice versa.
        """
        assert (
            await client.post(STAGING_URL, json=PUSH, headers={HEADER: TOKEN})
        ).json()["error"] == "unauthorised"
        assert (
            await client.post(URL, json=PUSH, headers={HEADER: STAGING_TOKEN})
        ).json()["error"] == "unauthorised"

    async def test_it_is_journalled_under_its_own_endpoint_name(
        self, client, journal, a_real_order
    ):
        """
        Which is how it is found in the admin: provider `slider`, endpoint
        `staging`. `LOG_RETENTION_DAYS` is 7, so it is a window on live traffic
        rather than an archive.
        """
        await client.post(STAGING_URL, json=PUSH, headers={HEADER: STAGING_TOKEN})

        (row,) = journal
        assert row.provider == "slider"
        assert row.endpoint == "staging"
        assert row.event_type == "delivered"
        assert row.order_number == "MM-1001"
        assert row.payload == PUSH

    async def test_an_unparseable_body_is_still_answered(self, client):
        response = await client.post(
            STAGING_URL,
            content=b"not json",
            headers={HEADER: STAGING_TOKEN, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
