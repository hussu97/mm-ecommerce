"""
Every setting the app reads must be listed in `docker-compose.prod.yml`.

That file's `environment:` block is an allow-list, not an `env_file`. A variable
written to `.env` on the VM and not named there never reaches the container, and
nothing anywhere reports it — the app simply sees its default.

That is not hypothetical. On 5 August 2026 production was found running with
noon Send entirely inert, no push notifications to any register, and the
Turnstile bot check switched off, because `NOON_SEND_*`, `APNS_*` and
`TURNSTILE_*` had been added to `.env.example`, to both deploy workflows and to
the documentation — the four places the repo's own checklist names — and not to
this fifth one. The secrets were all present on the VM. The compose file decided
otherwise, silently, for weeks.

So the checklist is enforced here rather than remembered.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core.config import Settings

COMPOSE = Path(__file__).resolve().parents[4] / "docker-compose.prod.yml"

#: Settings that genuinely never come from the environment on the VM. Each one
#: needs a reason, because "it is fine that this is missing" is the exact
#: sentence that caused the outage above.
NOT_FROM_ENV = {
    # Composed in the compose file itself from the POSTGRES_* parts.
    "DATABASE_URL",
    "REDIS_URL",
    # Postgres' own credentials, consumed by the database service.
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    # Local development only.
    "NEXT_PRIVATE_API_HOST",
}


class _NoDuplicateKeys(yaml.SafeLoader):
    """
    A loader that refuses a mapping with the same key twice.

    PyYAML's default silently keeps the last value, and that silence cost a
    production deploy on 8 August 2026. A block of `ZIINA_*` variables was
    inserted into the `api` service twice instead of once into `api` and once
    into `pos-api`. `yaml.safe_load` read it happily, every test here passed,
    and the deploy then died on the VM:

        failed to parse docker-compose.prod.yml: yaml: construct errors:
          line 80: mapping key "ZIINA_ENABLED" already defined at line 64

    Docker Compose parses with Go's yaml.v3, which treats a duplicate key as an
    error. So the file this suite was reading and the file production reads were
    not the same file, and the one place that disagreement shows up is the one
    place it cannot be fixed quickly.

    Two things were wrong and both are fixed here: the duplicate itself, and a
    test that could not have seen it.
    """

    def construct_mapping(self, node, deep=False):
        seen: set = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise AssertionError(
                    f"duplicate key {key!r} at line {key_node.start_mark.line + 1} "
                    "— Docker Compose refuses to parse this file"
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def _load_compose() -> dict:
    return yaml.load(COMPOSE.read_text(), Loader=_NoDuplicateKeys)


@pytest.fixture(scope="module")
def api_env() -> dict:
    return _load_compose()["services"]["api"]["environment"]


def test_the_compose_file_parses_the_way_docker_parses_it():
    """
    Not a redundant check on top of the fixtures above.

    They read one service each and would go on working with a duplicate key in
    the other. This reads the whole document under the strict loader, which is
    what the VM effectively does, and is the assertion that would have caught
    the 8 August deploy failure locally in under a second.
    """
    compose = _load_compose()

    assert set(compose["services"]) >= {"api", "pos-api", "postgres", "nginx"}


def test_the_register_gets_the_payment_gateway_credentials_too():
    """
    `pos-api` shares `Settings`, so it needs every gateway variable to boot —
    and it is the service the duplicate-key bug left without any of them, since
    both copies of the block landed in `api`.
    """
    pos_env = _load_compose()["services"]["pos-api"]["environment"]

    for key in (
        "ZIINA_ENABLED",
        "ZIINA_API_KEY",
        "ZIINA_WEBHOOK_SECRET",
        "ZIINA_API_URL",
        "ZIINA_TEST_MODE",
        "ZIINA_TIMEOUT_SECONDS",
    ):
        assert key in pos_env, f"{key} never reaches the register"


def test_every_setting_can_be_configured(api_env):
    """
    The whole point of a setting is that a deployment can change it. One the
    compose file does not pass through is a constant wearing a costume.
    """
    declared = set(Settings.model_fields) - NOT_FROM_ENV
    missing = sorted(declared - set(api_env))

    assert not missing, (
        "these settings cannot be configured on production — add them to the "
        f"`api` service in docker-compose.prod.yml: {missing}"
    )


def test_the_courier_and_push_credentials_reach_the_app(api_env):
    """
    Named individually because these are the ones whose absence is silent: an
    unset key reads as "this integration is switched off", which is a legitimate
    state and therefore indistinguishable from a mistake.
    """
    for key in (
        "NOON_SEND_API_KEY",
        "NOON_SEND_WEBHOOK_API_KEY",
        "LALAMOVE_API_KEY",
        "APNS_KEY_P8",
        "TURNSTILE_SECRET_KEY",
    ):
        assert key in api_env, f"{key} never reaches the container"


def test_the_register_can_be_given_push_credentials():
    """`/devices/push-token` is served by the register API, not the storefront one."""
    compose = yaml.safe_load(COMPOSE.read_text())
    pos_env = compose["services"]["pos-api"]["environment"]
    for key in ("APNS_KEY_P8", "APNS_KEY_ID", "APNS_TEAM_ID"):
        assert key in pos_env


def test_no_default_here_charges_money_the_code_does_not(api_env):
    """
    `LALAMOVE_SPECIAL_REQUESTS` used to default to `DOOR_TO_DOOR` in this file,
    which put the AED 5 door-to-door charge back on every booking whenever the
    variable was left empty — which is exactly how it is deliberately set, since
    dropping that charge was the point. A default here silently overrides the
    one in `Settings`, and this one cost five dirhams a delivery.
    """
    assert "DOOR_TO_DOOR" not in api_env["LALAMOVE_SPECIAL_REQUESTS"]
