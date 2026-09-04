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


# ── the settle window, and saying which capture failure it was ─────────────────
# The 2026-09-04 careem recovery ended here: the login finally authenticated
# (partners.careem.com/home, authed=True) and then the bearer capture came back
# empty. The surfaces are SPA page URLs and the bearer rides the /api/saturn-ext/
# call the bundle fires AFTER boot, but `wait_until="commit"` returns long before
# that — so a 4s settle navigated away mid-boot, every round, on a box that had
# just needed 45-90s to render a login form.


def test_bearer_settle_is_polled_so_a_fast_capture_returns_at_once(monkeypatch):
    """Widening the window must not slow the good case: a bearer landing two
    ticks in returns then, not `settle_seconds` later."""
    ticks = {"n": 0}
    captured: dict[str, str] = {}

    async def _sleep(_s):
        ticks["n"] += 1
        if ticks["n"] == 2:
            captured["authorization"] = "Bearer tok"

    monkeypatch.setattr(asyncio, "sleep", _sleep)
    page = _FakePage(captured, arrive_on_goto=99)
    asyncio.run(_await_careem_bearer(page, captured, rounds=6, settle_seconds=30))
    assert ticks["n"] == 2, "returned on the tick the bearer arrived"
    assert page.gotos == 0, "never navigated away from the surface that fired it"


def test_bearer_settle_default_is_wide_enough_for_a_slow_spa_boot():
    from aggregator_bootstrap.browser import _CAREEM_BEARER_SETTLE_SECONDS

    assert _CAREEM_BEARER_SETTLE_SECONDS >= 15


def test_bearer_guard_reports_that_nothing_fired():
    """Empty `captured` = the SPA never issued the call. Distinguishing this from
    'fired unauthenticated' is the difference between widening the settle and
    hunting a different bug."""
    with pytest.raises(NeedsHumanLogin) as exc:
        _assert_careem_bearer_captured("careem", {})
    assert "no saturn-ext request seen" in str(exc.value)


def test_bearer_guard_reports_which_headers_did_arrive():
    with pytest.raises(NeedsHumanLogin) as exc:
        _assert_careem_bearer_captured("careem", {"user-agent": "x", "uuid": "y"})
    msg = str(exc.value)
    assert "user-agent" in msg and "uuid" in msg


# ── keeta auto-login is wired into the daemon's relogin path ───────────────────


def test_keeta_is_a_wired_auto_login_channel():
    """Keeta login is a plain email -> password flow (no OTP, no mandatory
    captcha), so the daemon's `login_with_account` must drive it rather than
    reject it as "not wired" — otherwise a signed-out keeta session waits for a
    human forever, which is exactly what stranded keeta from 2026-09-01. The
    fallback still holds: a risk-triggered wall raises AntiBotChallengeError and
    the channel drops to needs-human, no worse than before."""
    import inspect

    from aggregator_bootstrap import browser as b
    from aggregator_bootstrap.channels import login as L

    src = inspect.getsource(b.login_with_account)
    # It is in the wired allow-list, and there is a keeta driving branch.
    gate = src.split("not wired")[0]
    assert '"keeta"' in gate, "keeta must be in the auto-login allow-list"
    assert "login_keeta(" in src, "login_with_account must drive login_keeta"

    # login_keeta takes the account's creds + a page (the daemon passes them);
    # it no longer reads only KEETA_EMAIL/KEETA_PASSWORD from the environment.
    params = inspect.signature(L.login_keeta).parameters
    assert {"email", "password", "page"} <= set(params), (
        "login_keeta must accept email/password/page so the daemon can drive it"
    )


def test_record_seen_keeps_api_paths_and_ignores_noise():
    from aggregator_bootstrap.browser import _CAREEM_SEEN_LIMIT, _record_seen

    seen: list[str] = []
    _record_seen(seen, "https://partners.careem.com/api/saturn-ext/merchant/x?a=1")
    _record_seen(seen, "https://cdn.careem.com/static/app.css")  # noise
    _record_seen(seen, "https://partners.careem.com/graphql")
    _record_seen(seen, "https://partners.careem.com/api/saturn-ext/merchant/x?a=2")
    assert seen == [
        "https://partners.careem.com/api/saturn-ext/merchant/x",  # query stripped
        "https://partners.careem.com/graphql",
    ], "distinct api paths only, no static noise, no duplicates"

    for i in range(_CAREEM_SEEN_LIMIT * 2):
        _record_seen(seen, f"https://x.careem.com/api/{i}")
    assert len(seen) <= _CAREEM_SEEN_LIMIT, "a chatty SPA must not grow this unbounded"


def test_bearer_guard_names_the_paths_the_page_really_called():
    """The decisive diagnosis. 'no saturn-ext request seen' rules out timing but
    not the possibility that Careem moved the API — this says which it is."""
    with pytest.raises(NeedsHumanLogin):
        _assert_careem_bearer_captured(
            "careem", {}, seen=["https://partners.careem.com/api/v2/merchant/x"]
        )
