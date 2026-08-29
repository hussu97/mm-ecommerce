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
    monkeypatch.setattr(cli, "_try_auto_relogin", lambda ch: calls.append(ch) or True)

    cli._run_for(["noon"], hydrate_first=False, auto_relogin=True)
    assert calls == ["noon"]  # the dead session was sent to re-login


def test_run_for_skips_relogin_when_disabled(monkeypatch):
    _patch_warm_raises(monkeypatch)
    called = False

    def _should_not_run(_ch):
        nonlocal called
        called = True
        return True

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

    assert cli._try_auto_relogin("talabat") is True
    assert seen["login"] == ("talabat", "ops@shop.ae", "ops@shop.ae")
    assert seen["push"][0] == "talabat"


def test_try_auto_relogin_returns_false_when_human_needed(monkeypatch):
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

    assert cli._try_auto_relogin("talabat") is False
    assert pushed is False  # nothing captured, so nothing pushed


def test_try_auto_relogin_skips_when_no_account(monkeypatch):
    import typer

    def _raise(ch, require_password=True):
        raise typer.BadParameter("no stored email")

    monkeypatch.setattr(cli, "_load_account", _raise)
    assert cli._try_auto_relogin("careem") is False
