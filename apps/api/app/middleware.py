"""The per-request envelope both apps wrap every HTTP request in.

One pure-ASGI middleware does what four `BaseHTTPMiddleware` layers used to
(`RequestIDMiddleware`, `LoggingMiddleware`, `MaxBodySizeMiddleware` and the
`add_security_headers` function): stamp a request id, refuse an oversized body,
write the structured access-log line and set the security headers.

They were merged because each `BaseHTTPMiddleware` layer costs real work on the
one event loop every request shares: it runs the rest of the stack in a new task
inside an anyio task group, rebuilds the response as a streaming response and
pumps every body chunk through a memory stream. Four of them stacked did that
four times per request, on a 2-vCPU box. A pure-ASGI layer only edits the
`http.response.start` message on its way out.

What a client sees is unchanged, header for header: the same `X-Request-ID`
(echoed from the request, or a fresh uuid4), the same 413 body, the same
`X-Content-Type-Options` / `X-Frame-Options` / production-only HSTS on every
response that passes through — including a 413, a 400 for an unknown Host and a
CORS preflight, because `app_setup.configure` keeps this layer outside both
`TrustedHostMiddleware` and `CORSMiddleware`. The log line keeps every field and
the same semantics: `latency` is measured to the response's first message (what
`call_next` returned on), a successful `/ping` is not logged, and an exception
that escapes the app is not logged here (`ServerErrorMiddleware` and the
unhandled-exception handler own that response and its log line).
"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger("mm.api")

MAX_BODY_BYTES = 10 * 1024 * 1024

_HSTS = "max-age=63072000; includeSubDomains; preload"


def _scrub_url(request: HTTPConnection) -> str:
    """The request URL with its query string redacted for logging.

    A logged `requestUrl` lands in GCP Cloud Logging, which is broadly
    readable and long-retained, so it must not carry a query string: our own
    URLs put an email in `?client_request_id=`/`?email=` lookups and a
    single-use token in the `/track` and password-reset links, and any of those
    in a log line is a plaintext leak of exactly the data we redact everywhere
    else. The path is always safe, so keep it verbatim and replace only the
    query with a fixed marker when one is present — enough to see that the
    request carried parameters without recording what they were.
    """
    url = request.url
    if not url.query:
        return str(url.replace(query=""))
    return str(url.replace(query="")) + "?<redacted>"


def _log_request(
    request: HTTPConnection,
    *,
    request_id: str,
    status: int,
    response_size: str | None,
    latency_s: float,
) -> None:
    """One structured line GCP Cloud Logging understands.

    The ``httpRequest`` key is a first-class GCP field — Cloud Logging renders
    it as a proper HTTP request entry with method, status, latency, etc.
    """
    # Docker probes both API processes every ten seconds. Nginx/Docker retain
    # the health state already, while logging each successful probe adds twelve
    # application log writes per minute. A failed probe remains actionable and
    # is therefore still logged below.
    if request.url.path == "/ping" and status < 400:
        return
    level = (
        logging.ERROR
        if status >= 500
        else logging.WARNING
        if status >= 400
        else logging.INFO
    )
    method = request.scope["method"]
    logger.log(
        level,
        "%s %s %s",
        method,
        request.url.path,
        status,
        extra={
            # GCP-native HTTP request structure
            "httpRequest": {
                "requestMethod": method,
                "requestUrl": _scrub_url(request),
                "status": status,
                "latency": f"{latency_s:.3f}s",
                "responseSize": response_size,
                "remoteIp": request.client.host if request.client else None,
                "userAgent": request.headers.get("user-agent", "")[:200],
                "protocol": request.scope.get("http_version", "HTTP/1.1"),
            },
            "request_id": request_id,
        },
    )


class RequestEnvelopeMiddleware:
    """Request id, body-size cap, access log and security headers — one layer.

    Per response, in the order the four old layers applied them (innermost
    first): the log line is written from the status and `content-length` the app
    produced, then `X-Request-ID`, then the security headers are set. "Set"
    means Starlette's `MutableHeaders` semantics — an existing header of the
    same name is replaced, not duplicated — exactly as `response.headers[...] =`
    did.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = HTTPConnection(scope)
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.perf_counter()

        async def send_with_envelope(message: Message) -> None:
            if message["type"] == "http.response.start":
                latency_s = time.perf_counter() - start
                headers = MutableHeaders(scope=message)
                _log_request(
                    request,
                    request_id=request_id,
                    status=message["status"],
                    response_size=headers.get("content-length"),
                    latency_s=latency_s,
                )
                headers["X-Request-ID"] = request_id
                # The full set, on the app rather than only in nginx's TLS
                # server blocks. They were `add_header` directives in
                # `nginx/conf.d/ssl.conf` and `pos.conf`, so the HTTP-only path
                # (`http.conf`, used before a certificate is issued) and any
                # response that did not traverse a TLS block carried none of
                # them. Setting them here means every response from either app
                # carries them regardless of the nginx path it took.
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                # HSTS only in production: it is an HTTPS-only instruction, and
                # asserting it over plain-HTTP local development would be wrong.
                # Value matches the nginx blocks (two years, subdomains,
                # preload). Read per request, as the old middleware did.
                if settings.is_production:
                    headers["Strict-Transport-Security"] = _HSTS
            await send(message)

        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_BODY_BYTES:
            response = JSONResponse(
                status_code=413, content={"detail": "Request body too large"}
            )
            await response(scope, receive, send_with_envelope)
            return

        await self.app(scope, receive, send_with_envelope)
