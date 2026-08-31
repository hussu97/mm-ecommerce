"""Unit tests for browser helpers — pure filesystem, no Playwright, no network."""

from __future__ import annotations

import asyncio

import pytest

from aggregator_bootstrap.browser import (
    ChromeLaunchError,
    NeedsHumanLogin,
    _assert_careem_bearer_captured,
    _await_careem_bearer,
    _clear_stale_singleton_locks,
    _wait_for_cdp,
)


def test_wait_for_cdp_raises_transient_launch_error(monkeypatch):
    """No debug port (dead display / crashed Chrome) is INFRA, not a human wall.

    It must raise `ChromeLaunchError` — which reauth maps to a short transient
    backoff — and NOT `NeedsHumanLogin`, which would flag the channel for an hour
    (the 2026-08-31 outage). Port 1 never answers, so the wait times out fast."""
    with pytest.raises(ChromeLaunchError):
        asyncio.run(_wait_for_cdp(1, timeout_s=0.5))
    # Guard the classification explicitly: a launch failure is not a human one.
    assert not issubclass(ChromeLaunchError, NeedsHumanLogin)


def test_clears_singleton_locks(tmp_path):
    """A profile left locked by a SIGKILLed warm is unblocked for the next launch."""
    (tmp_path / "SingletonLock").symlink_to("dead-host-123")
    (tmp_path / "SingletonCookie").write_text("x")
    (tmp_path / "SingletonSocket").write_text("y")
    (tmp_path / "Default").mkdir()  # real profile data must survive

    _clear_stale_singleton_locks(tmp_path)

    assert not (tmp_path / "SingletonLock").exists()
    assert not (tmp_path / "SingletonCookie").exists()
    assert not (tmp_path / "SingletonSocket").exists()
    assert (tmp_path / "Default").is_dir()


def test_no_locks_is_a_noop(tmp_path):
    """A clean profile (or a first-ever launch) clears nothing and does not raise."""
    _clear_stale_singleton_locks(tmp_path)  # must not raise on missing files
    assert list(tmp_path.iterdir()) == []


def test_clears_an_unexpected_singleton_variant(tmp_path):
    """Globbing Singleton* survives a Chrome-version rename and spares real data."""
    (tmp_path / "SingletonLock").symlink_to("dead-host-1")
    (tmp_path / "SingletonFoo").write_text("z")  # a hypothetical future variant
    (tmp_path / "Cookies").write_text("real")  # must NOT be touched

    _clear_stale_singleton_locks(tmp_path)

    assert not (tmp_path / "SingletonLock").exists()
    assert not (tmp_path / "SingletonFoo").exists()
    assert (tmp_path / "Cookies").read_text() == "real"


def test_spawn_chrome_clears_locks_before_launch(tmp_path, monkeypatch):
    """The raw debug-port launch (auto-relogin/heal) must clear a stale lock too,
    not just the Playwright warm — the fix for the recurring 'did not open a debug
    port' that had to be cleared by hand."""
    import subprocess

    from aggregator_bootstrap import browser

    profile = tmp_path / "noon.chrome"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("dead-host-9")  # left by a killed warm

    monkeypatch.setattr(browser, "chrome_binary", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(browser, "standalone_chrome_args", lambda **k: ["chrome"])

    class _FakeProc:
        pass

    launched = {}

    def _fake_popen(args, **kwargs):
        # The lock must already be gone by the time Chrome is actually spawned.
        launched["lock_present_at_spawn"] = (profile / "SingletonLock").exists()
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    proc = browser._spawn_chrome(profile=profile, port=45001, url="https://x")

    assert isinstance(proc, _FakeProc)
    assert launched["lock_present_at_spawn"] is False, (
        "stale SingletonLock was not cleared before Chrome was spawned"
    )
    assert not (profile / "SingletonLock").exists()


# ── careem bearer capture (the reauth 401 fix) ──────────────────────────────
def test_careem_guard_raises_without_a_bearer():
    """A careem session with cookies but no Authorization must NOT be pushed live:
    it would 401 on every ingest yet look healthy, so heal never retries it."""
    with pytest.raises(NeedsHumanLogin):
        _assert_careem_bearer_captured("careem", {"user-agent": "x"})


def test_careem_guard_passes_with_a_bearer():
    _assert_careem_bearer_captured("careem", {"authorization": "Bearer t"})  # no raise


def test_guard_is_a_noop_for_other_channels():
    # talabat/noon replay their token from a cookie/scope header, not this profile.
    _assert_careem_bearer_captured("talabat", {"user-agent": "x"})  # no raise


class _FakePage:
    """A page whose Nth goto makes the bearer 'arrive' in the shared dict."""

    def __init__(self, captured, arrive_on_goto):
        self.captured = captured
        self.arrive_on_goto = arrive_on_goto
        self.gotos = 0

    async def goto(self, url, **kwargs):
        self.gotos += 1
        if self.gotos >= self.arrive_on_goto:
            self.captured["authorization"] = "Bearer tok"


def test_await_bearer_returns_immediately_when_already_captured(monkeypatch):
    """On entry with the bearer already in hand, no sleep and no reload."""
    slept = []
    monkeypatch.setattr(asyncio, "sleep", lambda s: slept.append(s) or _done())
    captured = {"authorization": "Bearer already"}
    page = _FakePage(captured, arrive_on_goto=99)
    asyncio.run(_await_careem_bearer(page, captured, rounds=6))
    assert page.gotos == 0
    assert slept == []


def test_await_bearer_reloads_until_the_header_lands(monkeypatch):
    """The reauth path: the bearer shows up only after a reload — the loop waits
    and reloads a business surface rather than giving up after one fixed sleep."""
    monkeypatch.setattr(asyncio, "sleep", lambda s: _done())
    captured: dict[str, str] = {}
    page = _FakePage(captured, arrive_on_goto=2)  # bearer arrives on the 2nd goto
    asyncio.run(_await_careem_bearer(page, captured, rounds=6))
    assert captured.get("authorization") == "Bearer tok"
    assert page.gotos == 2  # stopped as soon as it landed, did not spin all 6


async def _done():
    return None
