from __future__ import annotations

from unittest.mock import AsyncMock, patch

from app.schemas.delivery import DeliveryQuoteResponse
from app.schemas.order_preview import OrderPreviewPromo, OrderPreviewResponse


def _preview(**overrides) -> OrderPreviewResponse:
    base = dict(
        subtotal=30.0,
        discount_amount=6.0,
        delivery_fee=20.0,
        low_order_fee=15.0,
        vat_rate=0.05,
        vat_amount=1.14,
        total_excl_vat=22.86,
        total=59.0,
        promo=OrderPreviewPromo(code="FIRST15", valid=True, discount_amount=6.0),
        delivery=DeliveryQuoteResponse(
            delivery_fee=20.0,
            base_fee=20.0,
            free_delivery_applied=False,
            remaining_for_free=120.0,
            in_known_zone=True,
            zone_name="Dubai",
        ),
    )
    return OrderPreviewResponse(**{**base, **overrides})


class TestOrderPreview:
    """
    The endpoint the checkout renders its money from. It computes nothing of its
    own any more — see `app/schemas/order_preview.py` — so what this router
    hands back *is* the summary the customer reads.
    """

    async def test_missing_body_returns_422(self, client):
        response = await client.post("/api/v1/orders/preview", json={})
        assert response.status_code == 422

    async def test_a_guest_prices_a_basket_without_signing_in(self, client):
        """
        Public, exactly like the delivery quote it replaces. A guest reaches the
        checkout with a session and no account, and a preview that demanded a
        token would leave that customer looking at a summary with no totals.
        """
        with patch(
            "app.api.v1.orders.order_service.preview_order",
            new=AsyncMock(return_value=_preview()),
        ) as preview:
            response = await client.post(
                "/api/v1/orders/preview",
                json={"delivery_method": "delivery", "promo_code": "FIRST15"},
                headers={"X-Session-Id": "sess_abc"},
            )

        assert response.status_code == 200
        # The session travels: without it there is no basket to price, because a
        # guest's cart is keyed on nothing else.
        assert preview.await_args.kwargs["session_id"] == "sess_abc"
        assert preview.await_args.args[2] is None, "no user id for a guest"

    async def test_it_answers_with_every_figure_the_summary_prints(self, client):
        with patch(
            "app.api.v1.orders.order_service.preview_order",
            new=AsyncMock(return_value=_preview()),
        ):
            response = await client.post(
                "/api/v1/orders/preview",
                json={"delivery_method": "delivery"},
            )

        body = response.json()
        # Every line on the checkout summary, and the VAT footnote under it.
        assert body["subtotal"] == 30.0
        assert body["discount_amount"] == 6.0
        assert body["delivery_fee"] == 20.0
        assert body["low_order_fee"] == 15.0
        assert body["total"] == 59.0
        assert body["vat_amount"] == 1.14
        # The delivery block rides along so the fee, the countdown and the
        # arrival estimate come from the same pricing call as the total.
        assert body["delivery"]["zone_name"] == "Dubai"
        assert body["promo"]["code"] == "FIRST15"

    async def test_an_unpriceable_address_is_an_answer_not_an_error(self, client):
        """
        The pay button refuses this; the preview must not. A checkout showing no
        total cannot explain why, and the explanation is the whole point of the
        amber notice next to it.
        """
        with patch(
            "app.api.v1.orders.order_service.preview_order",
            new=AsyncMock(
                return_value=_preview(
                    delivery_fee=None,
                    total=39.0,
                    delivery=DeliveryQuoteResponse(
                        delivery_fee=None,
                        free_delivery_applied=False,
                        free_delivery_available=False,
                        remaining_for_free=0.0,
                        in_known_zone=False,
                        serviceable=False,
                    ),
                )
            ),
        ):
            response = await client.post(
                "/api/v1/orders/preview",
                json={
                    "delivery_method": "delivery",
                    "latitude": "0",
                    "longitude": "0",
                },
            )

        assert response.status_code == 200
        assert response.json()["delivery_fee"] is None
        assert response.json()["delivery"]["serviceable"] is False


class TestOrdersEndpoints:
    async def test_create_order_missing_body_returns_422(self, client):
        # No auth required (get_optional_user), but body is required
        response = await client.post("/api/v1/orders", json={})
        assert response.status_code == 422

    async def test_list_my_orders_without_auth_returns_401(self, client):
        response = await client.get("/api/v1/orders")
        assert response.status_code == 401

    async def test_list_admin_orders_without_auth_returns_401(self, client):
        response = await client.get("/api/v1/orders/admin/all")
        assert response.status_code == 401

    async def test_update_order_status_without_auth_returns_401(self, client):
        response = await client.put(
            "/api/v1/orders/MM-2026-0001/status",
            json={"status": "confirmed"},
        )
        assert response.status_code == 401

    async def test_track_order_missing_fields_returns_422(self, client):
        response = await client.post("/api/v1/orders/track", json={})
        assert response.status_code == 422

    async def test_track_order_not_found_returns_404(self, client, mock_db):
        from unittest.mock import MagicMock

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        response = await client.post(
            "/api/v1/orders/track",
            json={"order_number": "MM-2026-9999", "email": "nobody@example.com"},
        )
        assert response.status_code == 404
