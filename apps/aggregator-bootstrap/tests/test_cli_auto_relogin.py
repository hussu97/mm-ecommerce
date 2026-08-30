"""The warm cron auto-recovers a dead session instead of waiting for a human.

A session the stored state no longer authenticates surfaces as `NeedsHumanLogin`
from the warm; `_run_for(auto_relogin=True)` must then drive the stored login and
push the fresh session, and only fall through to the operator when even that
can't finish unattended.
"""

from __future__ import annotations

from types import SimpleNamespace

from aggregator_bootstrap import cli
from aggregator_bootstrap.browser import NeedsHumanLogin


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
    monkeypatch.setattr(cli, "_load_account", lambda ch, require_password=True: account)

    seen = {}

    async def _login(channel, *, email, password, mailbox):
        seen["login"] = (channel, email, mailbox)
        return {"ok": True}

    async def _push(channel, result):
        seen["push"] = (channel, result)

    monkeypatch.setattr(cli, "login_with_account", _login)
    monkeypatch.setattr(cli, "push_probe", _push)

    assert cli._try_auto_relogin("talabat") is cli.ReloginOutcome.OK
    assert seen["login"] == ("talabat", "ops@shop.ae", "ops@shop.ae")
    assert seen["push"][0] == "talabat"


def test_try_auto_relogin_returns_needs_human_when_human_needed(monkeypatch):
    account = SimpleNamespace(email="ops@shop.ae", password="pw", mailbox=None)
    monkeypatch.setattr(cli, "_load_account", lambda ch, require_password=True: account)

    async def _login(channel, *, email, password, mailbox):
        raise NeedsHumanLogin("captcha")

    pushed = False

    async def _push(channel, result):
        nonlocal pushed
        pushed = True

    monkeypatch.setattr(cli, "login_with_account", _login)
    monkeypatch.setattr(cli, "push_probe", _push)

    assert cli._try_auto_relogin("talabat") is cli.ReloginOutcome.NEEDS_HUMAN
    assert pushed is False  # nothing captured, so nothing pushed


def test_try_auto_relogin_skips_when_no_account(monkeypatch):
    import typer

    def _raise(ch, require_password=True):
        raise typer.BadParameter("no stored email")

    monkeypatch.setattr(cli, "_load_account", _raise)
    assert cli._try_auto_relogin("careem") is cli.ReloginOutcome.NEEDS_HUMAN


# ── reauth backoff (guardrail: a dead channel must not be re-driven every tick) ──
def _stub_backoff_report(monkeypatch) -> list:
    """Capture the worker→API backoff reports and keep the tests offline."""
    reports: list = []

    async def _rep(channel, backoff_until):
        reports.append((channel, backoff_until))

    monkeypatch.setattr(cli.push, "report_reauth_backoff", _rep)
    return reports


def test_reauth_backoff_arms_and_blocks_then_clears(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.settings, "STORAGE_STATE_DIR", str(tmp_path))
    reports = _stub_backoff_report(monkeypatch)
    ch = "talabat"
    # No state → eligible immediately.
    assert cli._reauth_cooldown_remaining(ch) == 0.0
    # A failure arms the backoff (>= base of 5 min) AND publishes it to the API.
    cli._record_reauth_failure(ch)
    remaining = cli._reauth_cooldown_remaining(ch)
    assert 0 < remaining <= cli._REAUTH_BACKOFF_BASE_SECONDS + 1
    assert reports[-1][0] == ch and reports[-1][1] is not None
    # A second failure grows it (exponential).
    cli._record_reauth_failure(ch)
    assert cli._reauth_cooldown_remaining(ch) > cli._REAUTH_BACKOFF_BASE_SECONDS
    # Success clears it — locally and on the API.
    cli._clear_reauth_backoff(ch)
    assert cli._reauth_cooldown_remaining(ch) == 0.0
    assert reports[-1] == (ch, None)


def test_reauth_backoff_is_capped(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)
    ch = "talabat"
    for _ in range(20):  # far past the cap
        cli._record_reauth_failure(ch)
    assert cli._reauth_cooldown_remaining(ch) <= cli._REAUTH_BACKOFF_MAX_SECONDS + 1


def test_clear_reauth_backoff_does_not_report_when_nothing_to_clear(
    monkeypatch, tmp_path
):
    """The 2-minute healthy-channel tick calls _clear_reauth_backoff constantly;
    it must not spam the API when there was no standing backoff to clear."""
    monkeypatch.setattr(cli.settings, "STORAGE_STATE_DIR", str(tmp_path))
    reports = _stub_backoff_report(monkeypatch)
    cli._clear_reauth_backoff("noon")  # no backoff file exists
    assert reports == []


def test_heal_once_skips_a_channel_in_backoff(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        cli.push,
        "pull_sessions",
        _async_return([{"channel": "talabat", "status": "needs_bootstrap"}]),
    )
    cli._record_reauth_failure("talabat")  # arm the backoff
    attempts: list[str] = []
    monkeypatch.setattr(
        cli, "_try_auto_relogin", lambda ch: attempts.append(ch) or True
    )
    healed = cli._heal_once()
    assert attempts == []  # skipped, because it is in backoff
    assert healed == 0


def _async_return(value):
    async def _f(*a, **k):
        return value

    return _f


def test_try_auto_relogin_returns_transient_on_a_flaky_error(monkeypatch):
    """A network blip / marketplace 5xx (any non-NeedsHumanLogin error) is TRANSIENT:
    a retry will likely succeed, so it must NOT be treated like a human-needed wall."""
    account = SimpleNamespace(email="ops@shop.ae", password="pw", mailbox="ops@shop.ae")
    monkeypatch.setattr(cli, "_load_account", lambda ch, require_password=True: account)

    async def _login(channel, *, email, password, mailbox):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(cli, "login_with_account", _login)
    assert cli._try_auto_relogin("careem") is cli.ReloginOutcome.TRANSIENT


def test_transient_backoff_is_far_shorter_than_the_human_backoff(monkeypatch, tmp_path):
    """The fix's core: a transient failure must not inherit the hour-long human
    backoff that kept a healable channel dead. One transient failure ≤ 5 min; one
    human failure ≥ the human base (5 min) and grows to an hour."""
    monkeypatch.setattr(cli.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)

    cli._record_reauth_failure("careem", transient=True)
    transient_wait = cli._reauth_cooldown_remaining("careem")
    assert 0 < transient_wait <= cli._REAUTH_TRANSIENT_MAX_SECONDS + 1
    cli._clear_reauth_backoff("careem")

    cli._record_reauth_failure("careem", transient=False)
    human_wait = cli._reauth_cooldown_remaining("careem")
    assert human_wait >= cli._REAUTH_BACKOFF_BASE_SECONDS - 1
    assert transient_wait < human_wait


def test_heal_once_uses_the_short_backoff_when_the_relogin_is_transient(
    monkeypatch, tmp_path
):
    """A transient re-login failure in the heal loop parks the channel for only the
    short backoff, so the very next tick can retry — not the hour that stranded
    careem/deliveroo."""
    monkeypatch.setattr(cli.settings, "STORAGE_STATE_DIR", str(tmp_path))
    _stub_backoff_report(monkeypatch)
    monkeypatch.setattr(
        cli.push,
        "pull_sessions",
        _async_return([{"channel": "deliveroo", "status": "needs_bootstrap"}]),
    )
    monkeypatch.setattr(
        cli, "_try_auto_relogin", lambda ch: cli.ReloginOutcome.TRANSIENT
    )

    healed = cli._heal_once()
    assert healed == 0
    assert (
        cli._reauth_cooldown_remaining("deliveroo")
        <= cli._REAUTH_TRANSIENT_MAX_SECONDS + 1
    )
