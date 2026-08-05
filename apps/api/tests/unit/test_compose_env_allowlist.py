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


@pytest.fixture(scope="module")
def api_env() -> dict:
    compose = yaml.safe_load(COMPOSE.read_text())
    return compose["services"]["api"]["environment"]


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
        "TRIAL_CUSTOMER_EMAILS",
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
