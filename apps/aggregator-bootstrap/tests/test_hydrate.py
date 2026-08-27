"""Hydrate local files from an API bundle — no network."""

from __future__ import annotations

import json

from aggregator_bootstrap.hydrate import apply_bundle
from aggregator_bootstrap.session_capture import split_browser_state


def test_apply_bundle_writes_playwright_and_session_storage(tmp_path, monkeypatch):
    from aggregator_bootstrap import config as cfg

    monkeypatch.setattr(cfg.settings, "STORAGE_STATE_DIR", str(tmp_path))

    bundle = {
        "channel": "deliveroo",
        "storage_state": {
            "playwright": {
                "cookies": [
                    {
                        "name": "token",
                        "value": "abc",
                        "domain": ".deliveroo.com",
                        "path": "/",
                    }
                ],
                "origins": [],
            },
            "session_storage": {
                "https://partner-hub.deliveroo.com": {"orgId": "497912"}
            },
        },
    }
    assert apply_bundle(bundle) is True
    state = json.loads((tmp_path / "deliveroo.session.json").read_text())
    assert state["cookies"][0]["name"] == "token"
    extra = json.loads((tmp_path / "deliveroo.extra.json").read_text())
    assert extra["https://partner-hub.deliveroo.com"]["orgId"] == "497912"


def test_apply_bundle_skips_a_row_with_no_storage_state():
    assert apply_bundle({"channel": "careem", "cookies": {"a": "b"}}) is False


def test_split_accepts_a_raw_playwright_blob():
    raw = {"cookies": [{"name": "x", "value": "1"}], "origins": []}
    playwright, extra = split_browser_state(raw)
    assert playwright["cookies"][0]["name"] == "x"
    assert extra == {}
