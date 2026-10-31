"""
The shared middleware stack: its order, and what every response carries.

`app_setup.configure` wraps both apps in, outermost first: PoolSaturation →
RequestEnvelope (request id, 413 cap, access log, security headers) →
TrustedHost → CORS. Two things are pinned here:

* **The order.** Starlette makes the LAST `add_middleware` call outermost; the
  stack used to be added "first is outermost", which left the Host check
  innermost — after CORS, so a forged-Host preflight was answered 200.
* **Equivalence with the four `BaseHTTPMiddleware` layers it replaced.** Every
  response that passes the envelope — a 413, a refused Host's 400, a CORS
  preflight, an `AppError` — still carries `X-Request-ID` and the security
  headers and is still logged with the same `httpRequest` fields.

Driven at the raw ASGI level so a request can carry any header (a 10 MB
`content-length` with no body, a forged Host) and the exact response messages
can be inspected.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app import app_setup
from app.app_setup import PoolSaturationMiddleware
from app.core.exceptions import NotFoundError
from app.middleware import MAX_BODY_BYTES, RequestEnvelopeMiddleware

pytestmark = pytest.mark.asyncio

HOST = "api.example.com"
ORIGIN = "https://shop.example.com"


def _build(monkeypatch, *, checkedout: int = 0) -> FastAPI:
    monkeypatch.setattr(
        app_setup,
        "engine",
        SimpleNamespace(pool=SimpleNamespace(checkedout=lambda: checkedout)),
    )
    app = FastAPI()
    app_setup.configure(app, allowed_hosts=[HOST], cors_origins=[ORIGIN])

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/framed")
    async def framed():
        # A header the envelope also sets: it is replaced, never duplicated.
        return JSONResponse({"ok": True}, headers={"X-Frame-Options": "SAMEORIGIN"})

    @app.get("/missing")
    async def missing():
        raise NotFoundError("Nope")

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    return app


async def _call(
    app,
    method: str,
    path: str,
    *,
    host: str = HOST,
    headers: dict[str, str] | None = None,
    query: str = "",
):
    """One request through the full ASGI stack → (status, headers, body)."""
    raw = [(b"host", host.encode())]
    raw += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query.encode(),
        "headers": raw,
        "client": ("203.0.113.9", 5555),
        "server": (host, 80),
    }
    sent: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    try:
        await app(scope, receive, send)
    except RuntimeError:
        # ServerErrorMiddleware re-raises after sending its 500.
        pass
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    header_list = [(k.decode(), v.decode()) for k, v in start["headers"]]
    return start["status"], header_list, body


def _get(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [v for k, v in headers if k == name.lower()]


def _assert_envelope(headers, *, request_id: str | None = None) -> None:
    ids = _get(headers, "x-request-id")
    assert len(ids) == 1
    if request_id is not None:
        assert ids == [request_id]
    assert _get(headers, "x-content-type-options") == ["nosniff"]
    assert _get(headers, "x-frame-options") == ["DENY"]


async def test_stack_order_outermost_first(monkeypatch):
    app = _build(monkeypatch)
    # `user_middleware[0]` is the outermost layer.
    assert [m.cls for m in app.user_middleware] == [
        PoolSaturationMiddleware,
        RequestEnvelopeMiddleware,
        TrustedHostMiddleware,
        CORSMiddleware,
    ]


async def test_plain_response_carries_request_id_and_security_headers(monkeypatch):
    app = _build(monkeypatch)
    status, headers, body = await _call(app, "GET", "/ok")
    assert status == 200 and body == b'{"ok":true}'
    _assert_envelope(headers)
    assert "Strict-Transport-Security".lower() not in dict(headers)


async def test_a_client_request_id_is_echoed(monkeypatch):
    app = _build(monkeypatch)
    _, headers, _ = await _call(app, "GET", "/ok", headers={"X-Request-ID": "abc-123"})
    _assert_envelope(headers, request_id="abc-123")


async def test_an_app_set_header_is_replaced_not_duplicated(monkeypatch):
    app = _build(monkeypatch)
    _, headers, _ = await _call(app, "GET", "/framed")
    assert _get(headers, "x-frame-options") == ["DENY"]


async def test_oversized_body_is_413_with_the_envelope(monkeypatch, caplog):
    app = _build(monkeypatch)
    with caplog.at_level(logging.INFO, logger="mm.api"):
        status, headers, body = await _call(
            app,
            "POST",
            "/ok",
            headers={"content-length": str(MAX_BODY_BYTES + 1), "Origin": ORIGIN},
        )
    assert status == 413
    assert body == b'{"detail":"Request body too large"}'
    assert _get(headers, "content-type") == ["application/json"]
    _assert_envelope(headers)
    # Refused before CORS, exactly as before: no CORS headers on a 413.
    assert not _get(headers, "access-control-allow-origin")
    assert any("POST /ok 413" in r.getMessage() for r in caplog.records)


async def test_a_body_at_the_limit_is_let_through(monkeypatch):
    app = _build(monkeypatch)
    status, _, _ = await _call(
        app, "GET", "/ok", headers={"content-length": str(MAX_BODY_BYTES)}
    )
    assert status == 200


async def test_unknown_host_is_400_with_the_envelope_and_no_cors(monkeypatch, caplog):
    app = _build(monkeypatch)
    with caplog.at_level(logging.INFO, logger="mm.api"):
        status, headers, body = await _call(
            app, "GET", "/ok", host="evil.example", headers={"Origin": ORIGIN}
        )
    assert status == 400 and body == b"Invalid host header"
    _assert_envelope(headers)
    # The Host check now runs before CORS, so a refused Host gets no CORS grant.
    assert not _get(headers, "access-control-allow-origin")
    assert any("GET /ok 400" in r.getMessage() for r in caplog.records)


async def test_preflight_is_answered_with_the_envelope(monkeypatch):
    app = _build(monkeypatch)
    status, headers, body = await _call(
        app,
        "OPTIONS",
        "/ok",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, X-Device-Token",
        },
    )
    assert status == 200 and body == b"OK"
    assert _get(headers, "access-control-allow-origin") == [ORIGIN]
    assert _get(headers, "access-control-allow-credentials") == ["true"]
    _assert_envelope(headers)


async def test_preflight_with_a_forged_host_is_refused(monkeypatch):
    app = _build(monkeypatch)
    status, headers, _ = await _call(
        app,
        "OPTIONS",
        "/ok",
        host="evil.example",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    # Was 200 (CORS answered before the Host check); now the Host check wins.
    assert status == 400
    assert not _get(headers, "access-control-allow-origin")
    _assert_envelope(headers)


async def test_app_error_carries_the_envelope_and_cors(monkeypatch):
    app = _build(monkeypatch)
    status, headers, body = await _call(
        app, "GET", "/missing", headers={"Origin": ORIGIN}
    )
    assert status == 404 and body == b'{"detail":"Nope"}'
    assert _get(headers, "access-control-allow-origin") == [ORIGIN]
    _assert_envelope(headers)


async def test_unhandled_error_keeps_its_cors_headers(monkeypatch):
    app = _build(monkeypatch)
    status, headers, body = await _call(app, "GET", "/boom", headers={"Origin": ORIGIN})
    assert status == 500
    assert body == b'{"detail":"An internal server error occurred"}'
    assert _get(headers, "access-control-allow-origin") == [ORIGIN]


async def test_hsts_rides_every_enveloped_response_in_production(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "APP_ENV", "production")
    app = _build(monkeypatch)
    for kwargs in (
        {"method": "GET", "path": "/ok"},
        {"method": "GET", "path": "/ok", "host": "evil.example"},
        {
            "method": "POST",
            "path": "/ok",
            "headers": {"content-length": str(MAX_BODY_BYTES + 1)},
        },
    ):
        _, headers, _ = await _call(app, **kwargs)
        assert _get(headers, "strict-transport-security") == [
            "max-age=63072000; includeSubDomains; preload"
        ]


async def test_saturation_still_sheds_outermost(monkeypatch):
    app = _build(monkeypatch, checkedout=999)
    # Even a forged Host is shed first, with no other layer having run.
    status, headers, _ = await _call(app, "GET", "/ok", host="evil.example")
    assert status == 503
    assert _get(headers, "retry-after") == ["2"]
    assert not _get(headers, "x-request-id")


async def test_access_log_line_keeps_every_field(monkeypatch, caplog):
    app = _build(monkeypatch)
    with caplog.at_level(logging.INFO, logger="mm.api"):
        await _call(
            app,
            "GET",
            "/ok",
            query="email=a%40b.com",
            headers={"X-Request-ID": "rid-1", "User-Agent": "u" * 300},
        )
    record = next(r for r in caplog.records if r.getMessage() == "GET /ok 200")
    assert record.levelno == logging.INFO
    assert record.request_id == "rid-1"
    http = record.httpRequest
    assert set(http) == {
        "requestMethod",
        "requestUrl",
        "status",
        "latency",
        "responseSize",
        "remoteIp",
        "userAgent",
        "protocol",
    }
    assert http["requestMethod"] == "GET"
    assert http["requestUrl"] == f"http://{HOST}/ok?<redacted>"
    assert http["status"] == 200
    assert http["latency"].endswith("s") and float(http["latency"][:-1]) >= 0
    assert http["responseSize"] == "11"
    assert http["remoteIp"] == "203.0.113.9"
    assert http["userAgent"] == "u" * 200
    assert http["protocol"] == "1.1"
