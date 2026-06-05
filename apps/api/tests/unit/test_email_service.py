from __future__ import annotations

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
