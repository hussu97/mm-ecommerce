"""Per-channel Microsoft Graph mailbox — no global EMAIL_MS_* pair."""

from __future__ import annotations

import pytest

from aggregator_bootstrap.graph_mail import GraphApp, GraphMailboxError
from aggregator_bootstrap.mailbox import uses_graph, wait_for_otp


def test_graph_app_is_built_from_that_channel_mailbox():
    app = GraphApp.from_mailbox(
        {
            "client_id": "app-talabat",
            "client_secret": "secret-talabat",
            "tenant": "consumers",
            "redirect_uri": "http://localhost",
        }
    )
    assert app.client_id == "app-talabat"
    assert app.client_secret == "secret-talabat"
    assert "app-talabat" in app.authorize_url(state="talabat")
    assert "app-noon" not in app.authorize_url(state="talabat")


def test_graph_app_rejects_a_mailbox_without_its_own_secret():
    with pytest.raises(GraphMailboxError):
        GraphApp.from_mailbox({"client_id": "app-only"})


def test_uses_graph_when_provider_says_so():
    assert uses_graph({"provider": "graph", "refresh_token": "rt"}) is True
    assert uses_graph({"provider": "imap", "host": "imap.example"}) is False
    assert uses_graph({"refresh_token": "rt"}) is True


async def test_wait_for_otp_uses_the_channel_mailbox_app(monkeypatch):
    seen: dict[str, str] = {}

    def _fake_otp(*, mailbox, refresh_token, **_kwargs):
        seen["client_id"] = str(mailbox.get("client_id") or "")
        seen["refresh_token"] = refresh_token
        return ("483920", None)

    monkeypatch.setattr(
        "aggregator_bootstrap.graph_mail.fetch_latest_otp", _fake_otp
    )
    code = await wait_for_otp(
        sender_filter="noon",
        subject_filter="verify",
        timeout=5,
        mailbox={
            "provider": "graph",
            "client_id": "app-noon",
            "client_secret": "secret-noon",
            "refresh_token": "rt-noon",
        },
        channel="noon",
    )
    assert code == "483920"
    assert seen["client_id"] == "app-noon"
    assert seen["refresh_token"] == "rt-noon"
