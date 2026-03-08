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
    """Logs method, path, status code, duration, and context for every request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - start) * 1000

        status = response.status_code
        level = (
            logging.ERROR
            if status >= 500
            else logging.WARNING
            if status >= 400
            else logging.INFO
        )
        logger.log(
            level,
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "query": str(request.url.query) if request.url.query else None,
                "status": status,
                "duration_ms": round(duration_ms, 1),
                "request_id": getattr(request.state, "request_id", "-"),
                "content_length": response.headers.get("content-length"),
                "client_ip": request.client.host if request.client else None,
                "user_agent": request.headers.get("user-agent", "")[:200],
            },
        )
        return response
