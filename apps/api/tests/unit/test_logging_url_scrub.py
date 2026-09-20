"""
`LoggingMiddleware` never logs a query string (F-OPS-36).

The logged `requestUrl` lands in GCP Cloud Logging, which is broadly readable and
long-retained. Our own URLs carry an email in a `?email=`/`?client_request_id=`
lookup and a single-use token in the `/track` and password-reset links, so the
raw query string in a log line is a plaintext leak of exactly the data redacted
everywhere else. `_scrub_url` keeps the (always-safe) path verbatim and replaces
the query with a fixed `<redacted>` marker when one is present.
"""

from __future__ import annotations

from starlette.requests import Request

from app.middleware import LoggingMiddleware, _scrub_url


def _request(path: str, query: str = "") -> Request:
    """A minimal ASGI GET request scope for `_scrub_url`."""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query.encode(),
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
        }
    )


def test_query_string_is_redacted_not_recorded():
    scrubbed = _scrub_url(_request("/track", "email=a%40b.com&token=secret123"))
    assert scrubbed == "http://testserver/track?<redacted>"
    # The actual parameter values must not survive anywhere in the string.
    assert "a%40b.com" not in scrubbed
    assert "secret123" not in scrubbed
    assert "email" not in scrubbed


def test_a_path_without_a_query_is_left_intact():
    assert _scrub_url(_request("/products/cake")) == "http://testserver/products/cake"


def test_only_the_query_is_dropped_the_path_survives():
    scrubbed = _scrub_url(_request("/orders/42", "client_request_id=abc"))
    assert scrubbed == "http://testserver/orders/42?<redacted>"


async def test_successful_ping_is_not_written_to_the_application_log(caplog):
    """Docker owns successful probe state; failures still belong in app logs."""
    middleware = LoggingMiddleware(lambda *_args: None)
    request = _request("/ping")
    with caplog.at_level("INFO", logger="mm.api"):
        response = await middleware.dispatch(request, _call_next(200))
    assert response.status_code == 200
    assert caplog.records == []


async def test_failed_ping_is_still_logged(caplog):
    middleware = LoggingMiddleware(lambda *_args: None)
    request = _request("/ping")
    with caplog.at_level("WARNING", logger="mm.api"):
        response = await middleware.dispatch(request, _call_next(503))
    assert response.status_code == 503
    assert any("GET /ping 503" in record.getMessage() for record in caplog.records)


def _call_next(status_code: int):
    from starlette.responses import Response

    async def call_next(_request):
        return Response("ok", status_code=status_code)

    return call_next
