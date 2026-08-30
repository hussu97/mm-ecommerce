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
    assert {"api", "api-green", "pos-api", "pos-api-green"} <= set(compose["services"])


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
    for svc in ("pos-api", "pos-api-green"):
        command = str(compose["services"][svc].get("command", ""))
        assert "app.pos_main:app" in command, f"{svc} is a second storefront API"
        assert "--timeout-graceful-shutdown 8" in command


def test_storefront_slots_finish_in_flight_requests():
    compose = yaml.safe_load(COMPOSE.read_text())
    for svc in ("api", "api-green"):
        command = str(compose["services"][svc].get("command", ""))
        assert "app.main:app" in command
        assert "--timeout-graceful-shutdown 8" in command
        assert compose["services"][svc].get("stop_grace_period") == "10s"
    for svc in ("pos-api", "pos-api-green"):
        assert compose["services"][svc].get("stop_grace_period") == "10s"


def test_nginx_bind_mounts_runtime_upstreams():
    compose = yaml.safe_load(COMPOSE.read_text())
    volumes = compose["services"]["nginx"].get("volumes") or []
    assert any(
        str(v).startswith("./nginx/runtime:/etc/nginx/runtime") for v in volumes
    ), "cutover rewrites nginx/runtime/upstreams.conf on the host; nginx must see it"


def test_nginx_does_not_require_api_slots_healthy():
    """
    After a cutover the idle colour is stopped. A health dependency on api or
    pos-api would block nginx from coming back, which is TLS for the whole shop.
    """
    compose = yaml.safe_load(COMPOSE.read_text())
    deps = compose["services"]["nginx"].get("depends_on") or {}
    if isinstance(deps, list):
        names = set(deps)
    else:
        names = set(deps)
    for svc in ("api", "api-green", "pos-api", "pos-api-green"):
        assert svc not in names, (
            f"nginx depends_on {svc}; a stopped idle slot would block it"
        )
    if isinstance(deps, dict):
        for name, spec in deps.items():
            cond = spec.get("condition") if isinstance(spec, dict) else None
            assert cond != "service_healthy" or name not in {
                "api",
                "api-green",
                "pos-api",
                "pos-api-green",
            }


def test_postgres_max_connections_stays_at_thirty():
    text = COMPOSE.read_text()
    assert "-c max_connections=30" in text
    assert "-c max_connections=50" not in text
    assert "-c max_connections=100" not in text


def test_api_healthcheck_start_period_covers_i18n_seed():
    """
    compose --wait fails the moment Docker marks the container unhealthy.
    /ping is served only after lifespan. The seed is one table load now, so
    60s is enough for import+seed on the e2-small; 180s was the N+1 workaround.
    """
    compose = yaml.safe_load(COMPOSE.read_text())
    for svc in ("api", "api-green", "pos-api", "pos-api-green"):
        period = compose["services"][svc]["healthcheck"]["start_period"]
        seconds = int(str(period).rstrip("s"))
        assert seconds >= 60, (
            f"{svc} start_period={period!r} is too short for seed+wait"
        )
        assert seconds <= 90, (
            f"{svc} start_period={period!r} is padded for the old N+1 seed"
        )
        interval = compose["services"][svc]["healthcheck"]["interval"]
        assert interval in ("10s", "10"), interval
        timeout = compose["services"][svc]["healthcheck"]["timeout"]
        assert timeout in ("5s", "5"), timeout
        assert compose["services"][svc]["healthcheck"]["retries"] == 5


def test_api_slots_do_not_re_pull_an_image_already_on_the_vm():
    """
    Deploy/rollback pull (or tag) :latest first. pull_policy: always then
    re-checked GHCR on alembic + each idle slot — three extra 10–36s waits.
    """
    compose = yaml.safe_load(COMPOSE.read_text())
    for svc in ("api", "api-green", "pos-api", "pos-api-green"):
        assert compose["services"][svc].get("pull_policy") == "missing", svc


def test_every_pos_setting_reaches_a_container():
    """
    Catches the general shape of the bug: a POS_* setting added to config and
    then never passed through, which fails silently at its default.
    """
    wired = set(_environment("api")) | set(_environment("pos-api"))
    missing = [name for name in _pos_settings() if name not in wired]
    assert not missing, f"declared but never passed to a container: {missing}"


def test_aggregator_worker_does_not_re_pull_on_every_cron():
    """
    Cron runs this 720×/day. pull_policy: always meant each tick GHCR-pulled
    a ~4.7GB image. missing still pulls when the local tag is absent or was
    retagged after a bootstrap image change.
    """
    compose = yaml.safe_load(COMPOSE.read_text())
    assert compose["services"]["aggregator-worker"].get("pull_policy") == "missing"


def test_certbot_has_a_memory_cap():
    """Uncapped against host RAM 1.93GiB; 64m is enough for renew + sleep."""
    compose = yaml.safe_load(COMPOSE.read_text())
    memory = (
        compose["services"]["certbot"]
        .get("deploy", {})
        .get("resources", {})
        .get("limits", {})
        .get("memory")
    )
    assert memory == "64m", memory


def test_register_idle_postgres_pool_is_smaller_than_the_storefront():
    """
    Both apps share database.py; compose is what gives the till a smaller
    idle pool so it does not keep 13 connections warm for a handful of
    terminals.
    """
    api = _environment("api")
    pos = _environment("pos-api")
    assert api["DATABASE_POOL_SIZE"] == "${DATABASE_POOL_SIZE:-5}"
    assert api["DATABASE_MAX_OVERFLOW"] == "${DATABASE_MAX_OVERFLOW:-8}"
    assert str(pos["DATABASE_POOL_SIZE"]) == "2"
    assert str(pos["DATABASE_MAX_OVERFLOW"]) == "3"
    green_api = _environment("api-green")
    green_pos = _environment("pos-api-green")
    assert green_api["DATABASE_POOL_SIZE"] == api["DATABASE_POOL_SIZE"]
    assert green_pos["DATABASE_POOL_SIZE"] == pos["DATABASE_POOL_SIZE"]
