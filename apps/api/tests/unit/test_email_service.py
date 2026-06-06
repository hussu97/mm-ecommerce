from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.schemas.order import OrderItemResponse, OrderResponse
from app.services import email_service


class TestEmailService:
    def test_order_tracking_url_includes_public_lookup_params(self, monkeypatch):
        monkeypatch.setattr(
            email_service.settings,
            "WEB_URL",
            "https://meltingmomentscakes.com/",
        )

        url = email_service._order_tracking_url(
            "MM-20260605-001",
            "Guest User+test@example.com",
        )

        assert url.startswith("https://meltingmomentscakes.com/en/track?")
        assert "order_number=MM-20260605-001" in url
        assert "email=Guest+User%2Btest%40example.com" in url

    def test_admin_order_url_points_to_order_detail(self, monkeypatch):
        monkeypatch.setattr(
            email_service.settings,
            "ADMIN_URL",
            "https://admin.meltingmomentscakes.com/",
        )

        url = email_service._admin_order_url("MM-20260606-001")

        assert url == "https://admin.meltingmomentscakes.com/orders/MM-20260606-001"

    def test_owner_order_notification_template_includes_details(self, monkeypatch):
        monkeypatch.setattr(
            email_service.settings,
            "ADMIN_URL",
            "https://admin.meltingmomentscakes.com",
        )
        order = OrderResponse(
            id=uuid.uuid4(),
            order_number="MM-20260606-001",
            user_id=None,
            email="guest@example.com",
            delivery_method=DeliveryMethodEnum.DELIVERY,
            delivery_fee=35.0,
            subtotal=120.0,
            discount_amount=10.0,
            total=145.0,
            status=OrderStatusEnum.CONFIRMED,
            promo_code_used="MM10",
            shipping_address_snapshot={
                "first_name": "Fatema",
                "last_name": "Akhtar",
                "phone": "+971500000000",
                "address_line_1": "Villa 1",
                "city": "Dubai",
                "emirate": "Dubai",
            },
            payment_method="stripe",
            payment_provider="stripe",
            payment_id="pi_test",
            vat_rate=0.05,
            vat_amount=6.9,
            total_excl_vat=138.1,
            notes="Please call before delivery",
            admin_notes=None,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            items=[
                OrderItemResponse(
                    id=uuid.uuid4(),
                    product_id=uuid.uuid4(),
                    product_name="Brownie Box",
                    product_sku="BR-BOX",
                    product_translations={},
                    quantity=2,
                    base_price=60.0,
                    options_price=0.0,
                    unit_price=60.0,
                    total_price=120.0,
                    selected_options_snapshot=[
                        {
                            "modifier_name": "Flavour",
                            "option_name": "Classic",
                            "option_price": 0,
                        }
                    ],
                )
            ],
        )

        html = email_service._render(
            "owner_order_notification.html",
            recipient_email="fatema_f@hotmail.co.uk",
            order=order,
            admin_order_url=email_service._admin_order_url(order.order_number),
        )

        assert "MM-20260606-001" in html
        assert "guest@example.com" in html
        assert "Brownie Box" in html
        assert "Classic" in html
        assert "Please call before delivery" in html
        assert "https://admin.meltingmomentscakes.com/orders/MM-20260606-001" in html
