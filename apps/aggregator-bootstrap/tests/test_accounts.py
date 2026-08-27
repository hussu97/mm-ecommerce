"""Account recipe overlay: env wins, DB is the default."""

import pytest

from aggregator_bootstrap.accounts import PortalAccount, from_env, overlay_env
from aggregator_bootstrap.channels.login import LoginError, _deliveroo_credentials


def test_overlay_env_fills_blank_db_row(monkeypatch):
    monkeypatch.setattr(
        "aggregator_bootstrap.accounts.settings.DELIVEROO_EMAIL", "from-env@x"
    )
    monkeypatch.setattr(
        "aggregator_bootstrap.accounts.settings.DELIVEROO_PASSWORD", "env-secret"
    )
    account = overlay_env(
        PortalAccount(channel="deliveroo", email="", password="", login_method="email_password")
    )
    assert account.email == "from-env@x"
    assert account.password == "env-secret"


def test_from_env_is_none_when_unset(monkeypatch):
    monkeypatch.setattr("aggregator_bootstrap.accounts.settings.DELIVEROO_EMAIL", "")
    monkeypatch.setattr("aggregator_bootstrap.accounts.settings.DELIVEROO_PASSWORD", "")
    assert from_env("deliveroo") is None


def test_deliveroo_credentials_prefer_passed_over_env(monkeypatch):
    monkeypatch.setattr(
        "aggregator_bootstrap.channels.login.settings.DELIVEROO_EMAIL", "env@x"
    )
    monkeypatch.setattr(
        "aggregator_bootstrap.channels.login.settings.DELIVEROO_PASSWORD", "env-pw"
    )
    email, password = _deliveroo_credentials("db@x", "db-pw")
    assert email == "db@x"
    assert password == "db-pw"


def test_deliveroo_credentials_refuse_a_blank_recipe(monkeypatch):
    monkeypatch.setattr(
        "aggregator_bootstrap.channels.login.settings.DELIVEROO_EMAIL", ""
    )
    monkeypatch.setattr(
        "aggregator_bootstrap.channels.login.settings.DELIVEROO_PASSWORD", ""
    )
    with pytest.raises(LoginError, match="not configured"):
        _deliveroo_credentials("", "")
