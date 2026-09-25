"""
Production must fail closed on the host and CORS allow-lists (F-OPS-22).

`ALLOWED_HOSTS` defaulted to `["*"]` and `CORS_ORIGINS=""` parsed to `[""]`, so a
missing value came up wide open (the Host check disabled) rather than refusing to
start. `validate_production_secrets` now rejects a wildcard or empty list for both
in production, so the container fails on boot — while the previous one still
serves — instead of serving the whole internet.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings

# A production config that is valid in every respect except the field under test,
# so the only error a case can raise is the one it is about.
_VALID = dict(
    APP_ENV="production",
    DATABASE_URL="postgresql+asyncpg://u:p@db:5432/prod",
    SECRET_KEY="a-real-secret-key-of-adequate-length-0123456789",
    STRIPE_SECRET_KEY="sk_live_x",
    STRIPE_WEBHOOK_SECRET="whsec_x",
    RESEND_API_KEY="re_x",
    ALLOWED_HOSTS=["api.meltingmomentscakes.com"],
    CORS_ORIGINS=["https://meltingmomentscakes.com"],
)


def _settings(**overrides) -> Settings:
    # `_env_file=None` so a developer's local `.env` cannot colour the result.
    return Settings(_env_file=None, **{**_VALID, **overrides})


def test_a_fully_specified_production_config_boots():
    _settings()  # must not raise


def test_wildcard_allowed_hosts_is_refused():
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        _settings(ALLOWED_HOSTS=["*"])


def test_empty_allowed_hosts_is_refused():
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        _settings(ALLOWED_HOSTS=[])


def test_blank_string_allowed_hosts_is_refused():
    # The `[""]` an empty env value parses to must not read as a real host.
    with pytest.raises(ValueError, match="ALLOWED_HOSTS"):
        _settings(ALLOWED_HOSTS=[""])


def test_wildcard_cors_is_refused():
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(CORS_ORIGINS=["*"])


def test_empty_cors_string_is_refused():
    # `CORS_ORIGINS=""` on the VM parses to `[""]` — a bogus credentialled origin.
    with pytest.raises(ValueError, match="CORS_ORIGINS"):
        _settings(CORS_ORIGINS="")


def test_development_keeps_its_permissive_defaults():
    # The wildcard is correct for local development; the guard is production-only.
    dev = Settings(_env_file=None, APP_ENV="development", ALLOWED_HOSTS=["*"])
    assert dev.ALLOWED_HOSTS == ["*"]


# ── Paymob: a half-switched-on gateway must not boot ─────────────────────────

_PAYMOB_CREDS = dict(
    PAYMOB_SECRET_KEY="egy_sk_live_x",
    PAYMOB_PUBLIC_KEY="egy_pk_live_x",
    PAYMOB_API_KEY="api_key_x",
    PAYMOB_HMAC_SECRET="hmac_x",
    PAYMOB_CARD_INTEGRATION_ID=12345,
    PAYMOB_CALLBACK_BASE_URL="https://api.meltingmomentscakes.com",
)


def test_paymob_enabled_without_its_secrets_is_refused():
    # The flag on and nothing else is a gateway the router could pick that
    # cannot verify the callback telling it a card was charged.
    with pytest.raises(ValueError, match="PAYMOB_ENABLED is set") as exc:
        _settings(PAYMOB_ENABLED=True)
    for name in _PAYMOB_CREDS:
        assert name in str(exc.value)


@pytest.mark.parametrize("missing", sorted(_PAYMOB_CREDS))
def test_paymob_enabled_with_any_one_secret_missing_is_refused(missing):
    creds = {**_PAYMOB_CREDS}
    creds[missing] = 0 if missing == "PAYMOB_CARD_INTEGRATION_ID" else ""
    with pytest.raises(ValueError, match=missing):
        _settings(PAYMOB_ENABLED=True, **creds)


def test_paymob_enabled_with_every_secret_boots():
    _settings(PAYMOB_ENABLED=True, **_PAYMOB_CREDS)  # must not raise


def test_paymob_off_needs_no_secrets():
    # The supported production state: built, wired, switched off.
    _settings(PAYMOB_ENABLED=False)  # must not raise
