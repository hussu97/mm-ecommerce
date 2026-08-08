"""
The webhook mounts, and what they refuse.

Every gateway is served by one function (`process_gateway_webhook`) reached from
four routes: `/payments/webhooks/{stripe,ziina,<any>}` and `/webhooks/{stripe,
ziina}`. The duplication is deliberate — production's Stripe webhook was
configured against `/webhooks/stripe` before the payments router had one, and
changing the URL of a live webhook buys nothing — so what needs pinning is that
all of them land on the same code path and pass the gateway through correctly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.exceptions import BadRequestError


@pytest.fixture
def recorded(monkeypatch):
    """
    Capture the `webhook_logs` rows a request would write, without a database.

    The recorder deliberately uses its own session so its row survives a
    rolled-back handler, which means it does not go through the `get_db`
    override the test client installs — it has to be stubbed at the factory.
    """
    rows: list = []

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


class TestWebhookEndpoints:
    async def test_stripe_webhook_alias_processes_event(self, client):
        with patch(
            "app.api.v1.payments.payment_service.handle_webhook",
            new=AsyncMock(return_value={"received": True}),
        ) as handler:
            response = await client.post(
                "/api/v1/webhooks/stripe",
                content=b'{"id":"evt_test"}',
                headers={"Stripe-Signature": "sig_test"},
            )

        assert response.status_code == 200
        assert response.json() == {"received": True}
        handler.assert_awaited_once()
        # The gateway is named by the route, not read out of the body. A payload
        # that could choose its own verifier is a payload that could pick the
        # one whose secret it knows.
        assert handler.await_args.args[1] == "stripe"

    async def test_stripe_webhook_alias_requires_signature(self, client):
        response = await client.post(
            "/api/v1/webhooks/stripe",
            content=b'{"id":"evt_test"}',
        )

        assert response.status_code == 400

    async def test_ziina_webhook_is_mounted_on_both_routers(self, client):
        """
        Ziina gets the pair from the start, so whichever of the two an operator
        reaches for when registering the URL with Ziina, they are right.
        """
        for path in ("/api/v1/webhooks/ziina", "/api/v1/payments/webhooks/ziina"):
            with patch(
                "app.api.v1.payments.payment_service.handle_webhook",
                new=AsyncMock(return_value={"received": True}),
            ) as handler:
                response = await client.post(path, content=b"{}")

            assert response.status_code == 200, path
            assert handler.await_args.args[1] == "ziina", path

    async def test_ziina_webhook_refuses_an_unsigned_push(self, client):
        """
        Unlike the courier webhooks, an unsigned payment event is refused rather
        than acknowledged. Confirming an order is not something a stranger who
        can guess a payment intent ID should be able to do, and a dropped event
        here is recoverable — Ziina retries, and the intent can be re-read.
        """
        response = await client.post("/api/v1/webhooks/ziina", content=b"{}")
        assert response.status_code == 400

    @pytest.mark.parametrize("gateway", ["paypal", "adyen", "notaprocessor"])
    async def test_an_unknown_gateway_is_a_404_not_an_ok(self, client, gateway):
        """
        Acknowledging a webhook for a gateway that does not exist is how a
        misconfigured URL looks healthy for a month.
        """
        response = await client.post(
            f"/api/v1/payments/webhooks/{gateway}", content=b"{}"
        )
        assert response.status_code == 404


class TestPaymentWebhooksAreAudited:
    """
    Every payment webhook lands in `webhook_logs`, whatever became of it.

    The couriers got this table because a push we could not see was a push that,
    as far as anyone could tell, never happened. Payments need it more: a
    courier push we lose costs tracking, and a payment event we lose is money
    that moved with the order none the wiser. That is not hypothetical — an SDK
    upgrade broke every `payment_intent.succeeded` for three days while Stripe's
    dashboard showed nothing but successful deliveries, and the only evidence on
    our side was container stdout, which a restart had taken.

    So the rows that matter most are the ones a success-only log would not
    contain: the forged signature, the event that matched no order, the handler
    that raised.
    """

    async def test_a_processed_event_is_recorded_with_what_it_did(
        self, client, recorded
    ):
        with patch(
            "app.api.v1.payments.payment_service.handle_webhook",
            new=AsyncMock(
                return_value={
                    "received": True,
                    "event_type": "payment_intent.succeeded",
                    "external_id": "pi_live_1",
                    "matched": True,
                    "order_number": "MM-20260808-001",
                }
            ),
        ):
            response = await client.post(
                "/api/v1/payments/webhooks/stripe",
                content=b'{"id":"evt_1"}',
                headers={"Stripe-Signature": "sig"},
            )

        assert response.status_code == 200
        (row,) = recorded
        assert row.provider == "stripe"
        assert row.event_type == "payment_intent.succeeded"
        assert row.external_id == "pi_live_1"
        assert row.order_number == "MM-20260808-001"
        assert row.matched is True
        assert row.signature_valid is True
        assert row.http_status == 200
        assert row.payload == {"id": "evt_1"}

    async def test_the_mount_that_answered_is_recorded(self, client, recorded):
        """
        Which of the two URLs a processor is actually calling. Both do identical
        work, so without this the question can only be answered by asking them.
        """
        for path, expected in (
            ("/api/v1/payments/webhooks/stripe", "payments"),
            ("/api/v1/webhooks/stripe", "webhooks"),
        ):
            with patch(
                "app.api.v1.payments.payment_service.handle_webhook",
                new=AsyncMock(return_value={"received": True}),
            ):
                await client.post(
                    path, content=b"{}", headers={"Stripe-Signature": "s"}
                )

        assert [row.endpoint for row in recorded] == ["payments", "webhooks"]

    async def test_a_forged_signature_is_recorded_as_such(self, client, recorded):
        """The row that means somebody is pushing fake payment events at us."""
        with patch(
            "app.api.v1.payments.payment_service.handle_webhook",
            new=AsyncMock(side_effect=BadRequestError("Invalid webhook signature")),
        ):
            response = await client.post(
                "/api/v1/payments/webhooks/ziina", content=b'{"event":"x"}'
            )

        assert response.status_code == 400
        (row,) = recorded
        assert row.signature_valid is False
        assert row.http_status == 400
        assert "Invalid webhook signature" in row.error

    async def test_a_malformed_body_is_not_counted_as_a_forgery(self, client, recorded):
        """
        `signature_valid = false` should keep meaning "somebody forged this".
        Diluting it with every unparseable body makes it useless as an alert.
        """
        with patch(
            "app.api.v1.payments.payment_service.handle_webhook",
            new=AsyncMock(
                side_effect=BadRequestError("Could not parse webhook payload")
            ),
        ):
            await client.post("/api/v1/payments/webhooks/ziina", content=b"not json")

        (row,) = recorded
        assert row.signature_valid is None
        # The body that would not parse is the most interesting one in the
        # table, so it is kept rather than dropped for failing to be JSON — as
        # text, wrapped by the recorder's own `_jsonable` so a JSONB column will
        # take it. Same treatment the couriers' empty connection test gets.
        assert row.payload == {"raw": "not json"}

    async def test_a_handler_that_raises_still_leaves_a_row(self, client, recorded):
        """
        The exact shape of the three-day outage. The handler failed, the request
        5xx'd, the transaction rolled back — and because the recorder holds its
        own session, the evidence survives all three.
        """
        with patch(
            "app.api.v1.payments.payment_service.handle_webhook",
            new=AsyncMock(side_effect=RuntimeError("AttributeError: get")),
        ):
            with pytest.raises(RuntimeError):
                await client.post(
                    "/api/v1/payments/webhooks/stripe",
                    content=b'{"id":"evt_1"}',
                    headers={"Stripe-Signature": "sig"},
                )

        (row,) = recorded
        assert row.http_status == 500
        assert "AttributeError" in row.error
        # Not guessed either way: a 500 can land on either side of the signature
        # check, and "we do not know" is a different fact from "it was valid".
        assert row.signature_valid is None

    async def test_an_event_that_matched_no_order_is_flagged(self, client, recorded):
        with patch(
            "app.api.v1.payments.payment_service.handle_webhook",
            new=AsyncMock(
                return_value={
                    "received": True,
                    "event_type": "payment_intent.succeeded",
                    "external_id": "pi_orphan",
                    "matched": False,
                }
            ),
        ):
            await client.post(
                "/api/v1/payments/webhooks/stripe",
                content=b"{}",
                headers={"Stripe-Signature": "sig"},
            )

        (row,) = recorded
        assert row.matched is False

    async def test_an_event_we_act_on_nothing_for_leaves_matched_null(
        self, client, recorded
    ):
        """
        Ziina pushes a status for every transition of a payment in progress. None
        of those looks for an order, so asserting `false` would bury the runs of
        genuine `false` that this column exists to surface.
        """
        with patch(
            "app.api.v1.payments.payment_service.handle_webhook",
            new=AsyncMock(
                return_value={
                    "received": True,
                    "event_type": "payment_intent.status.updated:pending",
                    "external_id": "pi_z1",
                    "applied": False,
                }
            ),
        ):
            await client.post("/api/v1/payments/webhooks/ziina", content=b"{}")

        (row,) = recorded
        assert row.matched is None
        assert row.external_id == "pi_z1"
