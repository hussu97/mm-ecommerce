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

import re
from pathlib import Path

import pytest
import yaml

from pydantic import TypeAdapter

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
        "SLIDER_API_KEY",
        "SLIDER_WEBHOOK_TOKEN",
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


def test_the_register_can_book_a_courier():
    """
    `POST /pos/orders/{id}/packed` is served by `pos-api`, and packing is what
    books the driver. Without the courier credentials in *this* container,
    `lalamove_service.is_enabled()` is False and every order packed at the
    counter falls back to "Courier is not configured; dispatch this order by
    hand" — while the same order dispatched from the admin console books
    normally, because that runs in `api`.

    The symptom is indistinguishable from a misconfigured delivery polygon,
    which is what it was mistaken for. This is the third time a variable that
    reached `api` and not `pos-api` has silently disabled a feature in
    production, so it is asserted rather than remembered.
    """
    pos_env = _load_compose()["services"]["pos-api"]["environment"]

    for key in (
        "LALAMOVE_API_KEY",
        "LALAMOVE_API_SECRET",
        "LALAMOVE_ENV",
        "LALAMOVE_MARKET",
        "NOON_SEND_API_KEY",
        "NOON_SEND_CLIENT_CODE",
        "NOON_SEND_ENV",
        "SLIDER_API_KEY",
        "SLIDER_ACCOUNT_ID",
        "SLIDER_ENV",
        # The gate, not a credential — and the one most easily forgotten. It
        # decides whether a Slider zone's order goes to Slider or back to the
        # courier that carried it before, so a register without it would route
        # the pilot account's order differently from the storefront depending
        # only on which door the order came through.
        "SLIDER_TRIAL_EMAILS",
    ):
        assert key in pos_env, (
            f"{key} never reaches the register, so an order packed at the "
            "counter cannot book a courier"
        )


def test_the_register_does_not_run_the_batch_sweeper():
    """
    The one courier-adjacent variable `pos-api` must *not* get.

    `BATCH_DISPATCHER_ENABLED` starts a background loop that sweeps due batches
    out to couriers. A second copy of it in the register container would race
    the storefront's over the same rows, and the losing side of that race is a
    second driver booked for one cake.
    """
    pos_env = _load_compose()["services"]["pos-api"]["environment"]
    assert "BATCH_DISPATCHER_ENABLED" not in pos_env


def _default_of(expression: str) -> str | None:
    """The `X` out of `${VAR:-X}`, or None if the entry has no default."""
    m = re.fullmatch(r"\$\{[A-Z0-9_]+:-(.*)\}", str(expression).strip())
    return m.group(1) if m else None


def test_both_services_agree_on_courier_configuration():
    """
    Every courier variable must carry the *same* default in both services.

    Four of these are arithmetic — NOON_SEND_BASE and NOON_SEND_BULKY_BASE are
    fares in dirhams whatever their names suggest, and DETOUR_FACTOR and
    SURGE_AED feed the same quote. A register quoting on different numbers from
    the storefront sells one delivery at two prices depending which door the
    order came through, and nothing errors to make anyone look.

    Written as a comparison rather than a list of expected values so a future
    change to the storefront's pricing cannot drift from the register's by
    being made in only one place.
    """
    compose = _load_compose()
    api_env = compose["services"]["api"]["environment"]
    pos_env = compose["services"]["pos-api"]["environment"]

    shared = [
        k
        for k in api_env
        if k.startswith(("LALAMOVE_", "NOON_SEND_", "SLIDER_")) and k in pos_env
    ]
    assert shared, "no courier variables reach the register at all"

    mismatched = {
        k: (api_env[k], pos_env[k]) for k in shared if api_env[k] != pos_env[k]
    }
    assert not mismatched, (
        "the register and the storefront disagree on courier configuration: "
        f"{mismatched}"
    )


def test_no_compose_default_is_a_value_its_setting_cannot_parse():
    """
    An empty default is not neutral.

    `${NOON_SEND_BASE:-}` passes "" to a `float` field; pydantic refuses it
    while building `Settings`, which happens at import, so the container does
    not start at all. That took the register down in production — a variable
    added to make a feature work instead removed the whole service, because
    the name read like a URL and the field was a fare.

    Scoped to the plain numeric and boolean settings. Those are the ones with
    no decoder and no before-validator between the environment and the field,
    so what compose writes is what pydantic must parse — and they are the whole
    class this went wrong in. Lists and strings are left alone: several parse
    "" or "*" through their own validators, legally and for years.
    """
    compose = _load_compose()
    failures = []

    for service in ("api", "pos-api"):
        env = compose["services"][service]["environment"]
        for key, expression in env.items():
            field = Settings.model_fields.get(key)
            if field is None or field.annotation not in (int, float, bool):
                continue
            default = _default_of(expression)
            if default is None:
                continue
            try:
                TypeAdapter(field.annotation).validate_python(default)
            except Exception as exc:  # noqa: BLE001 - reporting, not handling
                failures.append(
                    f"{service}.{key} defaults to {default!r}, which is not a "
                    f"valid {field.annotation.__name__}: {type(exc).__name__}"
                )

    assert not failures, (
        "a compose default cannot be parsed by its setting, so the container "
        "will fail to start:\n  " + "\n  ".join(failures)
    )
