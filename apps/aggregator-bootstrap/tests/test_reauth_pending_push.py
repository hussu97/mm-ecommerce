"""A captured session whose API push fails is parked and retried HTTP-only —
never re-driven as a fresh headed login (F-AGG-12).

Before this, a 500 on the push returned TRANSIENT, and the heal loop drove
another headed Chrome 30s-5min later — burning a login (and a Careem reCAPTCHA /
Talabat PerimeterX challenge) on a session already captured. Now the bundle is
parked to `<channel>.pending_push.json` and `_drain_pending_pushes` re-pushes it.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from aggregator_bootstrap import reauth


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    return tmp_path


def _stub_login(monkeypatch, result=None):
    account = SimpleNamespace(email="ops@shop.ae", password="pw", mailbox="ops@shop.ae")
    monkeypatch.setattr(
        reauth, "_load_account", lambda ch, require_password=True: account
    )

    async def _login(channel, *, email, password, mailbox):
        return result if result is not None else {"probe": channel}

    monkeypatch.setattr(reauth, "login_with_account", _login)
    # payload_from_probe would need a real probe object; stand it in.
    monkeypatch.setattr(
        reauth, "payload_from_probe", lambda ch, result: {"channel": ch, "session": "x"}
    )


def test_a_push_failure_parks_the_session_and_returns_push_pending(
    storage, monkeypatch
):
    _stub_login(monkeypatch)

    async def _push_fails(channel, result):
        raise RuntimeError("API 500")

    monkeypatch.setattr(reauth, "push_probe", _push_fails)

    outcome = reauth._try_auto_relogin("careem")

    assert outcome is reauth.ReloginOutcome.PUSH_PENDING
    parked = storage / "careem.pending_push.json"
    assert parked.exists(), "the captured session was parked for retry"
    assert json.loads(parked.read_text())["payload"]["channel"] == "careem"


def test_drain_repushes_a_parked_session_then_clears_it(storage, monkeypatch):
    (storage / "careem.pending_push.json").write_text(
        json.dumps({"parked_at": time.time(), "payload": {"channel": "careem"}})
    )
    pushed: list = []

    async def _push_ok(payload):
        pushed.append(payload)
        return {"status": "live"}

    monkeypatch.setattr(reauth.push, "push_session", _push_ok)

    drained = reauth._drain_pending_pushes()

    assert drained == 1
    assert pushed == [{"channel": "careem"}]
    assert not (storage / "careem.pending_push.json").exists()
    # A drained session is as good as a fresh login — the success floor is stamped.
    assert (storage / "careem.last_relogin.json").exists()


def test_drain_keeps_a_still_failing_park(storage, monkeypatch):
    (storage / "careem.pending_push.json").write_text(
        json.dumps({"parked_at": time.time(), "payload": {"channel": "careem"}})
    )

    async def _push_fails(payload):
        raise RuntimeError("API still down")

    monkeypatch.setattr(reauth.push, "push_session", _push_fails)

    drained = reauth._drain_pending_pushes()

    assert drained == 0
    assert (storage / "careem.pending_push.json").exists(), "kept for the next tick"


def test_drain_discards_a_stale_park(storage, monkeypatch):
    old = time.time() - reauth._PENDING_PUSH_MAX_AGE_SECONDS - 1
    (storage / "careem.pending_push.json").write_text(
        json.dumps({"parked_at": old, "payload": {"channel": "careem"}})
    )

    async def _push_should_not_run(payload):
        raise AssertionError("a stale park must be discarded, not pushed")

    monkeypatch.setattr(reauth.push, "push_session", _push_should_not_run)

    drained = reauth._drain_pending_pushes()

    assert drained == 0
    assert not (storage / "careem.pending_push.json").exists()


def test_heal_drains_and_does_not_relogin_a_channel_with_a_parked_push(
    storage, monkeypatch
):
    # A dead channel per the API, but a fresh session is already parked.
    (storage / "careem.pending_push.json").write_text(
        json.dumps({"parked_at": time.time(), "payload": {"channel": "careem"}})
    )

    async def _pull():
        return [{"channel": "careem", "unusable_reason": "session dead"}]

    monkeypatch.setattr(reauth.push, "pull_sessions", _pull)

    async def _push_still_down(payload):
        raise RuntimeError("API still down")

    monkeypatch.setattr(reauth.push, "push_session", _push_still_down)

    def _must_not_relogin(ch):
        raise AssertionError("a parked session must not trigger a headed relogin")

    monkeypatch.setattr(reauth, "_try_auto_relogin", _must_not_relogin)

    healed = reauth._heal_once()

    assert healed == 0  # push still down, but critically: no headed login was driven
