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
        level = (
            logging.ERROR
            if status >= 500
            else logging.WARNING
            if status >= 400
            else logging.INFO
        )

        url = str(request.url)
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
