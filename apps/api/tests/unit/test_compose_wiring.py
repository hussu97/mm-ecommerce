"""
Settings that gate behaviour have to reach the container.

`POS_REQUIRE_POS_HOST=true` was written into the production `.env`, and the
storefront API went on accepting device tokens anyway: the variable was passed
to the register's container but not to the one that has to *refuse* them. The
flag was set, the deploy was green, and the boundary it existed to enforce was
simply absent.

A setting is only real once something passes it through, so that is what this
checks.
"""

from __future__ import annotations

import pathlib

import pytest

yaml = pytest.importorskip("yaml", reason="pyyaml is needed to read the compose file")

COMPOSE = pathlib.Path(__file__).parents[3].parent / "docker-compose.prod.yml"

pytestmark = pytest.mark.skipif(not COMPOSE.exists(), reason=f"{COMPOSE} not found")


def _environment(service: str) -> dict:
    compose = yaml.safe_load(COMPOSE.read_text())
    return compose["services"][service].get("environment", {}) or {}


def _pos_settings() -> list[str]:
    from app.core.config import Settings

    return [name for name in Settings.model_fields if name.startswith("POS_")]


def test_the_compose_file_is_readable():
    compose = yaml.safe_load(COMPOSE.read_text())
    assert {"api", "pos-api"} <= set(compose["services"])


def test_the_storefront_api_can_refuse_device_tokens():
    """
    The refusal happens on the storefront side, so the flag has to be there.
    Setting it only on the register left it doing nothing.
    """
    assert "POS_REQUIRE_POS_HOST" in _environment("api")


def test_both_apps_agree_on_the_rule():
    api = _environment("api").get("POS_REQUIRE_POS_HOST")
    pos = _environment("pos-api").get("POS_REQUIRE_POS_HOST")
    assert api == pos, "the two apps must not disagree about where tokens work"


def test_the_register_gets_its_own_host_and_cors_lists():
    env = _environment("pos-api")
    assert "POS_ALLOWED_HOSTS" in env
    assert "POS_CORS_ORIGINS" in env


def test_the_register_runs_the_register_app():
    compose = yaml.safe_load(COMPOSE.read_text())
    command = str(compose["services"]["pos-api"].get("command", ""))
    assert "app.pos_main:app" in command, "otherwise it is a second storefront API"


def test_every_pos_setting_reaches_a_container():
    """
    Catches the general shape of the bug: a POS_* setting added to config and
    then never passed through, which fails silently at its default.
    """
    wired = set(_environment("api")) | set(_environment("pos-api"))
    missing = [name for name in _pos_settings() if name not in wired]
    assert not missing, f"declared but never passed to a container: {missing}"
