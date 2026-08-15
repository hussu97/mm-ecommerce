from __future__ import annotations

__all__ = [
    "AppError",
    "BadGatewayError",
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "ServiceUnavailableError",
    "UnauthorizedError",
    "UnprocessableError",
]


class AppError(Exception):
    """Base application error."""

    status_code: int = 500

    def __init__(
        self,
        detail: str = "An unexpected error occurred",
        *,
        code: str | None = None,
        payload: dict | None = None,
    ):
        self.detail = detail
        #: A stable identifier for clients that must branch on *which* failure
        #: this is, rather than merely report it.
        #:
        #: Optional, and deliberately rare. A message is for a person and is
        #: free to be reworded; a code is a promise to a client and cannot be.
        #: It exists because the register had to tell "this device has been
        #: unpaired" apart from every other 401 — it was treating all of them as
        #: an unpairing and throwing away a perfectly good device token on a
        #: misconfigured host or a missing header.
        self.code = code
        #: A structured body to send instead of the message. When set, the
        #: handler renders it as the response's `detail` — the same
        #: string-or-object slot FastAPI's own `HTTPException` uses, which the
        #: admin's `ApiError` already parses both ways. It exists for the
        #: Lalamove re-quote 409, whose body carries the fresh quote alongside
        #: the message so the dialog can offer it rather than only apologise.
        #: `detail` stays the human sentence either way, so logs and `str(exc)`
        #: keep reading like an error and not like JSON.
        self.payload = payload
        super().__init__(detail)


class NotFoundError(AppError):
    status_code = 404

    def __init__(self, detail: str = "Resource not found"):
        super().__init__(detail)


class BadRequestError(AppError):
    status_code = 400

    def __init__(self, detail: str = "Bad request"):
        super().__init__(detail)


class UnauthorizedError(AppError):
    status_code = 401

    def __init__(self, detail: str = "Unauthorized", *, code: str | None = None):
        super().__init__(detail, code=code)


class ForbiddenError(AppError):
    status_code = 403

    def __init__(self, detail: str = "Forbidden"):
        super().__init__(detail)


class ConflictError(AppError):
    status_code = 409

    def __init__(self, detail: str = "Conflict", *, payload: dict | None = None):
        super().__init__(detail, payload=payload)


class UnprocessableError(AppError):
    status_code = 422

    def __init__(self, detail: str = "Unprocessable entity"):
        super().__init__(detail)


class BadGatewayError(AppError):
    """Something upstream — a courier, a payment gateway, object storage —
    declined or could not be reached. 502 rather than 500 because the failure
    is theirs, and the message is written for the person reading it."""

    status_code = 502

    def __init__(self, detail: str = "Upstream service failed"):
        super().__init__(detail)


class ServiceUnavailableError(AppError):
    """A feature is switched off or unconfigured in this deployment, so the
    request cannot be served here at all — as opposed to a `BadGatewayError`,
    where we tried and the other side failed."""

    status_code = 503

    def __init__(self, detail: str = "Service unavailable"):
        super().__init__(detail)
