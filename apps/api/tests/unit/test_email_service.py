from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

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

        # Through `_order_context`, which is what the real send does — the
        # template reads flattened item rows and totals rather than the ORM
        # shapes, so rendering it with a bare order proves nothing about what an
        # owner actually receives.
        html = email_service._render(
            "owner_order_notification.html",
            recipient_email="fatema_f@hotmail.co.uk",
            admin_order_url=email_service._admin_order_url(order.order_number),
            customer_name="Fatema Akhtar",
            customer_phone="+971500000000",
            **email_service._order_context(order),
        )

        assert "MM-20260606-001" in html
        assert "guest@example.com" in html
        assert "Brownie Box" in html
        assert "Classic" in html
        assert "Please call before delivery" in html
        assert "https://admin.meltingmomentscakes.com/orders/MM-20260606-001" in html


# ── nobody to write to ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "address", ["", "   ", None, "not-an-address", "@example.com", "someone@"]
)
def test_an_unusable_address_is_skipped_not_failed(address, monkeypatch):
    """
    A counter sale has no customer email, so cancelling one reaches the mailer
    with nothing to send to.

    Skipped, not failed. Before this, the empty string went to Resend, came
    back `Invalid \\`to\\` field`, and was logged at error — five orders with no
    customer to notify became five alerts on a board that should only carry
    surprises. And `None` never reached Resend at all: it raised
    `AttributeError` on `.lower()` first.
    """
    called = []
    monkeypatch.setattr(
        email_service.resend.Emails, "send", lambda *a, **k: called.append(a)
    )

    result = email_service._send(
        address, "Order Cancelled — POS-B001-0004", "<p>hi</p>"
    )

    assert result["status"] == "skipped"
    assert result["resend_id"] is None
    assert called == [], "an address that cannot work was still sent to Resend"


def test_a_real_address_still_sends(monkeypatch):
    """The guard must not swallow the ordinary case."""
    monkeypatch.setattr(email_service.settings, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(
        email_service.resend.Emails,
        "send",
        lambda params: SimpleNamespace(id="re_123"),
    )

    result = email_service._send("customer@example.com", "Your order", "<p>hi</p>")

    assert result["status"] == "sent"
    assert result["resend_id"] == "re_123"
