"""The warm path auto-recovers a dead session instead of waiting for a human.

A session the stored state no longer authenticates surfaces as `NeedsHumanLogin`
from the warm; `cli._run_for(auto_relogin=True)` must then drive the stored login
and push the fresh session, and only fall through to the operator when even that
can't finish unattended.

The re-login / backoff / heal logic itself was moved to `reauth.py` (Phase 3) so
the `serve` daemon can share it; `cli` re-exposes only what its commands need.
These tests target `reauth` for the moved helpers and `cli` for `_run_for`.
"""

from __future__ import annotations

from types import SimpleNamespace

from aggregator_bootstrap import cli, reauth
from aggregator_bootstrap.browser import ChromeLaunchError, NeedsHumanLogin


def _patch_warm_raises(monkeypatch):
    async def _warm(channel):
        raise NeedsHumanLogin(f"{channel} session dead")

    monkeypatch.setattr(cli, "warm_channel", _warm)


def test_run_for_escalates_dead_session_to_auto_relogin(monkeypatch):
    _patch_warm_raises(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(
        cli, "_try_auto_relogin", lambda ch: calls.append(ch) or cli.ReloginOutcome.OK
    )

    cli._run_for(["noon"], hydrate_first=False, auto_relogin=True)
    assert calls == ["noon"]  # the dead session was sent to re-login


def test_run_for_skips_relogin_when_disabled(monkeypatch):
    _patch_warm_raises(monkeypatch)
    called = False

    def _should_not_run(_ch):
        nonlocal called
        called = True
        return cli.ReloginOutcome.OK

    monkeypatch.setattr(cli, "_try_auto_relogin", _should_not_run)
    cli._run_for(["noon"], hydrate_first=False, auto_relogin=False)
    assert called is False  # warm-only behaviour preserved


def test_try_auto_relogin_drives_stored_login_and_pushes(monkeypatch):
    account = SimpleNamespace(email="ops@shop.ae", password="pw", mailbox="ops@shop.ae")
    monkeypatch.setattr(
        reauth, "_load_account", lambda ch, require_password=True: account
    )

    seen = {}

    async def _login(channel, *, email, password, mailbox):
        seen["login"] = (channel, email, mailbox)
        return {"ok": True}

    async def _push(channel, result):
        seen["push"] = (channel, result)

    monkeypatch.setattr(reauth, "login_with_account", _login)
    monkeypatch.setattr(reauth, "push_probe", _push)

    assert reauth._try_auto_relogin("talabat") is reauth.ReloginOutcome.OK
    assert seen["login"] == ("talabat", "ops@shop.ae", "ops@shop.ae")
    assert seen["push"][0] == "talabat"


def test_try_auto_relogin_returns_needs_human_when_human_needed(monkeypatch):
    account = SimpleNamespace(email="ops@shop.ae", password="pw", mailbox=None)
    monkeypatch.setattr(
        reauth, "_load_account", lambda ch, require_password=True: account
    )

    async def _login(channel, *, email, password, mailbox):
        raise NeedsHumanLogin("captcha")

    pushed = False

    async def _push(channel, result):
        nonlocal pushed
        pushed = True

    monkeypatch.setattr(reauth, "login_with_account", _login)
    monkeypatch.setattr(reauth, "push_probe", _push)

    assert reauth._try_auto_relogin("talabat") is reauth.ReloginOutcome.NEEDS_HUMAN
    assert pushed is False  # nothing captured, so nothing pushed


def test_try_auto_relogin_treats_browser_launch_failure_as_transient(monkeypatch):
    """A dead/mid-restart display ("did not open a debug port") is INFRA, not a
    human wall: it must earn the SHORT transient backoff so the heal loop retries
    once the Xvfb supervisor is back — not the hour-long needs-human backoff that
    left every channel dead on 2026-08-31."""
    account = SimpleNamespace(email="ops@shop.ae", password="pw", mailbox="ops@shop.ae")
    monkeypatch.setattr(
        reauth, "_load_account", lambda ch, require_password=True: account
    )

    async def _login(channel, *, email, password, mailbox):
        raise ChromeLaunchError("Chrome did not open a debug port on 45001")

    pushed = False

    async def _push(channel, result):
        nonlocal pushed
        pushed = True

    monkeypatch.setattr(reauth, "login_with_account", _login)
    monkeypatch.setattr(reauth, "push_probe", _push)

    assert reauth._try_auto_relogin("deliveroo") is reauth.ReloginOutcome.TRANSIENT
    assert pushed is False


def test_try_auto_relogin_skips_when_no_account(monkeypatch):
    import typer

    def _raise(ch, require_password=True):
        raise typer.BadParameter("no stored email")

    monkeypatch.setattr(reauth, "_load_account", _raise)
    assert reauth._try_auto_relogin("careem") is reauth.ReloginOutcome.NEEDS_HUMAN


# ── reauth backoff (guardrail: a dead channel must not be re-driven every tick) ──
def _stub_backoff_report(monkeypatch) -> list:
    """Capture the worker→API backoff reports and keep the tests offline."""
    reports: list = []

    async def _rep(channel, backoff_until):
        reports.append((channel, backoff_until))

    monkeypatch.setattr(reauth.push, "report_reauth_backoff", _rep)
    return reports


def test_reauth_backoff_arms_and_blocks_then_clears(monkeypatch, tmp_path):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    reports = _stub_backoff_report(monkeypatch)
    ch = "talabat"
    # No state → eligible immediately.
    assert reauth._reauth_cooldown_remaining(ch) == 0.0
    # A failure arms the backoff (>= base of 5 min) AND publishes it to the API.
    reauth._record_reauth_failure(ch)
    remaining = reauth._reauth_cooldown_remaining(ch)
    assert 0 < remaining <= reauth._REAUTH_BACKOFF_BASE_SECONDS + 1
    assert reports[-1][0] == ch and reports[-1][1] is not None
    # A second failure grows it (exponential).
    reauth._record_reauth_failure(ch)
    assert reauth._reauth_cooldown_remaining(ch) > reauth._REAUTH_BACKOFF_BASE_SECONDS
    # Success clears it — locally and on the API.
    reauth._clear_reauth_backoff(ch)
    assert reauth._reauth_cooldown_remaining(ch) == 0.0
    assert reports[-1] == (ch, None)


def test_reauth_backoff_is_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)
    ch = "talabat"
    for _ in range(20):  # far past the cap
        reauth._record_reauth_failure(ch)
    assert (
        reauth._reauth_cooldown_remaining(ch) <= reauth._REAUTH_BACKOFF_MAX_SECONDS + 1
    )


def test_clear_reauth_backoff_does_not_report_when_nothing_to_clear(
    monkeypatch, tmp_path
):
    """The healthy-channel heal tick calls _clear_reauth_backoff constantly; it must
    not spam the API when there was no standing backoff to clear."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    reports = _stub_backoff_report(monkeypatch)
    reauth._clear_reauth_backoff("noon")  # no backoff file exists
    assert reports == []


def test_heal_once_skips_a_channel_in_backoff(monkeypatch, tmp_path):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        reauth.push,
        "pull_sessions",
        _async_return([{"channel": "talabat", "status": "needs_bootstrap"}]),
    )
    reauth._record_reauth_failure("talabat")  # arm the backoff
    attempts: list[str] = []
    monkeypatch.setattr(
        reauth, "_try_auto_relogin", lambda ch: attempts.append(ch) or True
    )
    healed = reauth._heal_once()
    assert attempts == []  # skipped, because it is in backoff
    assert healed == 0


def test_heal_once_skips_a_server_refreshable_channel(monkeypatch, tmp_path):
    """A server-refreshable channel (Deliveroo) is renewed by the API itself over
    httpx before every sweep; the worker must NOT burn a headed Chrome re-logging
    it in. The API marks the bundle `server_refreshable` from the one auth
    descriptor, and the heal loop leaves it alone."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        reauth.push,
        "pull_sessions",
        _async_return(
            [
                {
                    "channel": "deliveroo",
                    "status": "needs_bootstrap",
                    "unusable_reason": "needs_bootstrap",
                    "server_refreshable": True,
                }
            ]
        ),
    )
    attempts: list[str] = []
    monkeypatch.setattr(
        reauth,
        "_try_auto_relogin",
        lambda ch: attempts.append(ch) or reauth.ReloginOutcome.OK,
    )
    assert reauth._heal_once() == 0
    assert attempts == []  # left to the API sweep — no headed relogin


def _async_return(value):
    async def _f(*a, **k):
        return value

    return _f


# ── success cooldown (guardrail: a healthy-but-short-lived session — Talabat's
#    rotating cookie — must not spawn a headed Chrome on every heal tick) ──────────
def test_success_cooldown_blocks_a_recently_refreshed_channel(monkeypatch, tmp_path):
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(reauth.settings, "WORKER_MIN_RELOGIN_INTERVAL_SECONDS", 900)
    ch = "talabat"
    # No stamp → eligible immediately.
    assert reauth._success_cooldown_remaining(ch) == 0.0
    # A recorded success arms the floor for ~the configured interval.
    reauth._record_relogin_success(ch)
    remaining = reauth._success_cooldown_remaining(ch)
    assert 0 < remaining <= 900 + 1
    # Disabling the floor (<=0) makes the channel eligible again at once.
    monkeypatch.setattr(reauth.settings, "WORKER_MIN_RELOGIN_INTERVAL_SECONDS", 0)
    assert reauth._success_cooldown_remaining(ch) == 0.0


def test_heal_once_skips_a_channel_refreshed_within_the_floor(monkeypatch, tmp_path):
    """A channel that just re-logged in successfully is NOT re-driven again while it
    is inside WORKER_MIN_RELOGIN_INTERVAL_SECONDS, even though the API reports it
    dead — this is the talabat headed-Chrome storm that pinned the e2-small's CPU."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(reauth.settings, "WORKER_MIN_RELOGIN_INTERVAL_SECONDS", 900)
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        reauth.push,
        "pull_sessions",
        _async_return([{"channel": "talabat", "status": "needs_bootstrap"}]),
    )
    reauth._record_relogin_success("talabat")  # just refreshed
    attempts: list[str] = []
    monkeypatch.setattr(
        reauth, "_try_auto_relogin", lambda ch: attempts.append(ch) or True
    )
    healed = reauth._heal_once()
    assert attempts == []  # skipped — inside the success floor
    assert healed == 0


def test_heal_once_stamps_success_so_the_next_tick_is_paced(monkeypatch, tmp_path):
    """A successful heal records the success stamp, so an immediate second heal that
    still finds the channel dead skips it instead of re-driving a headed Chrome."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(reauth.settings, "WORKER_MIN_RELOGIN_INTERVAL_SECONDS", 900)
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        reauth.push,
        "pull_sessions",
        _async_return([{"channel": "talabat", "status": "needs_bootstrap"}]),
    )
    attempts: list[str] = []
    monkeypatch.setattr(
        reauth,
        "_try_auto_relogin",
        lambda ch: attempts.append(ch) or reauth.ReloginOutcome.OK,
    )
    assert reauth._heal_once() == 1  # first heal drives it and succeeds
    assert reauth._heal_once() == 0  # second heal is paced by the success stamp
    assert attempts == ["talabat"]  # driven exactly once, not on every tick


def test_success_stamp_does_not_block_a_transient_failure_retry(monkeypatch, tmp_path):
    """The floor is stamped ONLY on success: a channel whose relogin FAILS leaves no
    stamp, so the short transient backoff still governs a prompt retry — recovery
    from a real blip is not slowed by this CPU guard."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(reauth.settings, "WORKER_MIN_RELOGIN_INTERVAL_SECONDS", 900)
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        reauth.push,
        "pull_sessions",
        _async_return([{"channel": "careem", "status": "needs_bootstrap"}]),
    )
    monkeypatch.setattr(
        reauth, "_try_auto_relogin", lambda ch: reauth.ReloginOutcome.TRANSIENT
    )
    reauth._heal_once()
    # No success stamp was written, so the success floor is not engaged; only the
    # short transient backoff paces the retry.
    assert reauth._success_cooldown_remaining("careem") == 0.0
    assert (
        0
        < reauth._reauth_cooldown_remaining("careem")
        <= reauth._REAUTH_TRANSIENT_MAX_SECONDS
    )


def test_try_auto_relogin_returns_transient_on_a_flaky_error(monkeypatch):
    """A network blip / marketplace 5xx (any non-NeedsHumanLogin error) is TRANSIENT:
    a retry will likely succeed, so it must NOT be treated like a human-needed wall."""
    account = SimpleNamespace(email="ops@shop.ae", password="pw", mailbox="ops@shop.ae")
    monkeypatch.setattr(
        reauth, "_load_account", lambda ch, require_password=True: account
    )

    async def _login(channel, *, email, password, mailbox):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(reauth, "login_with_account", _login)
    assert reauth._try_auto_relogin("careem") is reauth.ReloginOutcome.TRANSIENT


def test_transient_backoff_is_far_shorter_than_the_human_backoff(monkeypatch, tmp_path):
    """The fix's core: a transient failure must not inherit the hour-long human
    backoff that kept a healable channel dead. One transient failure ≤ 5 min; one
    human failure ≥ the human base (5 min) and grows to an hour."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)

    reauth._record_reauth_failure("careem", transient=True)
    transient_wait = reauth._reauth_cooldown_remaining("careem")
    assert 0 < transient_wait <= reauth._REAUTH_TRANSIENT_MAX_SECONDS + 1
    reauth._clear_reauth_backoff("careem")

    reauth._record_reauth_failure("careem", transient=False)
    human_wait = reauth._reauth_cooldown_remaining("careem")
    assert human_wait >= reauth._REAUTH_BACKOFF_BASE_SECONDS - 1
    assert transient_wait < human_wait


def test_needs_human_failure_emits_a_distinct_alert_log(monkeypatch, tmp_path, caplog):
    """A human-needed reauth failure emits a greppable AGGREGATOR_NEEDS_HUMAN ERROR
    (the VM's log-based alerting pages on it); a transient failure must NOT."""
    import logging

    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)

    with caplog.at_level(logging.ERROR, logger="aggregator-bootstrap"):
        reauth._record_reauth_failure("talabat", transient=False)
    human = [r for r in caplog.records if "AGGREGATOR_NEEDS_HUMAN" in r.getMessage()]
    assert len(human) == 1
    assert "talabat" in human[0].getMessage()
    assert human[0].levelno == logging.ERROR

    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="aggregator-bootstrap"):
        reauth._record_reauth_failure("careem", transient=True)
    assert not [r for r in caplog.records if "AGGREGATOR_NEEDS_HUMAN" in r.getMessage()]


def test_heal_once_uses_the_short_backoff_when_the_relogin_is_transient(
    monkeypatch, tmp_path
):
    """A transient re-login failure in the heal loop parks the channel for only the
    short backoff, so the very next tick can retry — not the hour that stranded
    careem/deliveroo."""
    monkeypatch.setattr(reauth.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        reauth.push,
        "pull_sessions",
        _async_return([{"channel": "deliveroo", "status": "needs_bootstrap"}]),
    )
    monkeypatch.setattr(
        reauth, "_try_auto_relogin", lambda ch: reauth.ReloginOutcome.TRANSIENT
    )

    healed = reauth._heal_once()
    assert healed == 0
    assert (
        reauth._reauth_cooldown_remaining("deliveroo")
        <= reauth._REAUTH_TRANSIENT_MAX_SECONDS + 1
    )
