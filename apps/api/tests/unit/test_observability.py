"""
Both apps report errors, and say which one they are.

When the register was split out, Sentry's `init` and the JSON log formatter
stayed behind in `main.py`, which `pos_main` does not import. The register ran
with neither: its logs reached Cloud Logging as unparsed text, and the
`capture_exception` in the shared error handler was a silent no-op. Every
error at a till went nowhere, and nothing about the deploy looked wrong.

Both now go through one function, so they cannot drift apart again.
"""

from __future__ import annotations

import inspect

from app import app_setup


def test_observability_is_set_up_in_one_place():
    assert callable(app_setup.configure_observability)


def test_both_apps_configure_it():
    import app.main as main
    import app.pos_main as pos_main

    for module, service in ((main, "mm-api"), (pos_main, "mm-pos-api")):
        source = inspect.getsource(module)
        assert f'configure_observability(service="{service}")' in source


def test_neither_app_initialises_sentry_on_its_own():
    """A second `init` elsewhere is how the two drifted apart the first time."""
    import app.main as main
    import app.pos_main as pos_main

    for module in (main, pos_main):
        assert "sentry_sdk.init" not in inspect.getsource(module)


def test_events_are_tagged_with_the_service():
    """
    Two apps share a database and a DSN, so "an error in production" is not
    actionable without knowing which one raised it.
    """
    source = inspect.getsource(app_setup.configure_observability)
    assert 'sentry_sdk.set_tag("service", service)' in source


def test_sentry_is_skipped_when_no_dsn_is_configured():
    source = inspect.getsource(app_setup.configure_observability)
    assert "if not settings.SENTRY_DSN:" in source


def test_pool_timeouts_all_share_one_fingerprint():
    """The QueuePool timeout is one fact — the pool ran dry — however many call
    sites it surfaces at, so it must collapse into a single Sentry issue rather
    than fan out one-per-stack-trace."""
    err = TimeoutError(
        "QueuePool limit of size 5 overflow 1 reached, connection timed out, "
        "timeout 30.00"
    )
    a = app_setup._group_pool_timeouts({}, {"exc_info": (type(err), err, None)})
    b = app_setup._group_pool_timeouts({}, {"exc_info": (type(err), err, None)})
    assert a["fingerprint"] == b["fingerprint"] == ["db-connection-pool-timeout"]


def test_other_errors_keep_their_default_grouping():
    err = ValueError("something unrelated")
    event = app_setup._group_pool_timeouts({}, {"exc_info": (type(err), err, None)})
    assert "fingerprint" not in event


def test_grouping_survives_an_event_with_no_exception():
    assert app_setup._group_pool_timeouts({"level": "info"}, {}) == {"level": "info"}


def test_production_logs_are_structured_for_cloud_logging():
    source = inspect.getsource(app_setup._configure_logging)
    assert "severity" in source, "GCP reads the level from this field"
    assert "stack_trace" in source, "Error Reporting reads the traceback from this"
    assert "uvicorn.access" in source, "or uvicorn writes plain text alongside it"


def test_the_register_really_does_switch_sentry_on():
    """
    The regression this file exists for: importing pos_main left the client
    inert, and nothing said so.
    """
    import sentry_sdk

    import app.pos_main  # noqa: F401  — importing is what configures it
    from app.core.config import settings

    client = sentry_sdk.get_client()
    if settings.SENTRY_DSN:
        assert client.is_active()
    else:
        # No DSN in the test environment; assert the wiring instead.
        assert 'configure_observability(service="mm-pos-api")' in inspect.getsource(
            app.pos_main
        )
