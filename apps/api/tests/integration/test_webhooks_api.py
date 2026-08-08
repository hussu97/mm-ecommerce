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
