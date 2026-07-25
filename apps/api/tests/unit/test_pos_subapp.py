"""
The register runs as its own application on its own hostname.

Two properties matter and are easy to lose in a refactor: the terminal's API
must not carry the storefront's surface, and a device token — a long-lived
credential sitting on a shop counter — must only work against the register.
"""

from __future__ import annotations

import inspect

from app.api.v1 import devices
from app.main import app as web_app
from app.pos_main import app as pos_app


def _paths(app) -> set[str]:
    """
    Every path an app serves, read from its OpenAPI schema.

    Not `app.routes`: FastAPI 0.140 stopped flattening `include_router` into
    it and keeps an opaque wrapper around the sub-router instead, so walking
    that attribute saw only the handful of endpoints declared directly on the
    app — and quietly reported the register as carrying nothing. The schema is
    the public description of what the app serves and is stable across both
    layouts.
    """
    return set(app.openapi()["paths"])


def test_the_register_does_not_carry_the_storefront():
    """
    A terminal has no use for carts, the CMS, the blog or bulk imports, and
    every one of those reachable from a shop counter is surface for nothing.
    """
    pos = _paths(pos_app)
    for absent in (
        "/api/v1/cart",
        "/api/v1/cms",
        "/api/v1/blog",
        "/api/v1/import",
        "/api/v1/bulk",
        "/api/v1/addresses",
        "/api/v1/analytics",
    ):
        assert not any(p.startswith(absent) for p in pos), f"{absent} reachable"


def test_the_register_carries_what_a_till_needs():
    pos = _paths(pos_app)
    for present in (
        "/api/v1/devices",
        "/api/v1/staff",
        "/api/v1/tills",
        "/api/v1/pos/orders",
        "/api/v1/products",
        "/api/v1/payment-methods",
        "/api/v1/printers",
        "/api/v1/menu-groups",
    ):
        assert any(p.startswith(present) for p in pos), f"{present} missing"


def test_the_register_is_smaller_than_the_main_api():
    assert len(_paths(pos_app)) < len(_paths(web_app))


def test_both_apps_answer_a_health_check():
    for app in (web_app, pos_app):
        assert "/health" in _paths(app)
        assert "/ping" in _paths(app)


def test_a_device_token_is_only_honoured_by_the_register():
    source = inspect.getsource(devices.get_current_device)
    assert 'request.app.state, "is_pos_app"' in source
    assert "only accepted by the register" in source


def test_the_host_restriction_is_a_deliberate_cutover():
    """
    Enforcing before pos.* resolves would strand every terminal with nowhere
    to authenticate, so the switch is config, not a side effect of a deploy.
    """
    from app.core.config import settings

    assert settings.POS_REQUIRE_POS_HOST is False, "must default off"
    source = inspect.getsource(devices.get_current_device)
    assert "settings.POS_REQUIRE_POS_HOST" in source
    assert "logger.warning" in source, "the old path must be visible in the logs"


def test_the_register_app_is_marked_and_the_storefront_is_not():
    assert getattr(pos_app.state, "is_pos_app", False) is True
    assert getattr(web_app.state, "is_pos_app", False) is False


def test_only_one_app_seeds_i18n():
    """Two processes racing the same upsert on boot can deadlock."""
    import app.pos_main as pos_main

    assert "seed=False" in inspect.getsource(pos_main)


def test_the_device_token_header_survives_a_preflight():
    from app import app_setup

    assert "X-Device-Token" in inspect.getsource(app_setup.configure)
