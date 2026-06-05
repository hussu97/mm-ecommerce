from __future__ import annotations

from unittest.mock import AsyncMock, patch


class TestWebhookEndpoints:
    async def test_stripe_webhook_alias_processes_event(self, client):
        with patch(
            "app.api.v1.payments.payment_service.handle_stripe_webhook",
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

    async def test_stripe_webhook_alias_requires_signature(self, client):
        response = await client.post(
            "/api/v1/webhooks/stripe",
            content=b'{"id":"evt_test"}',
        )

        assert response.status_code == 400
