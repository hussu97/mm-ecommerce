from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("mm.api")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attaches a unique X-Request-ID header to every request and response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


def _scrub_url(request: Request) -> str:
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


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs every request as a structured line GCP Cloud Logging understands.

    The ``httpRequest`` key is a first-class GCP field — Cloud Logging renders
    it as a proper HTTP request entry with method, status, latency, etc.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        latency_s = time.perf_counter() - start

        status = response.status_code
        # Docker probes both API processes every ten seconds. Nginx/Docker retain
        # the health state already, while logging each successful probe adds twelve
        # application log writes per minute. A failed probe remains actionable and
        # is therefore still logged below.
        if request.url.path == "/ping" and status < 400:
            return response
        level = (
            logging.ERROR
            if status >= 500
            else logging.WARNING
            if status >= 400
            else logging.INFO
        )

        url = _scrub_url(request)
        logger.log(
            level,
            "%s %s %s",
            request.method,
            request.url.path,
            status,
            extra={
                # GCP-native HTTP request structure
                "httpRequest": {
                    "requestMethod": request.method,
                    "requestUrl": url,
                    "status": status,
                    "latency": f"{latency_s:.3f}s",
                    "responseSize": response.headers.get("content-length"),
                    "remoteIp": request.client.host if request.client else None,
                    "userAgent": request.headers.get("user-agent", "")[:200],
                    "protocol": request.scope.get("http_version", "HTTP/1.1"),
                },
                "request_id": getattr(request.state, "request_id", "-"),
            },
        )
        return response
