from __future__ import annotations

__all__ = [
    "AppError",
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "UnauthorizedError",
    "UnprocessableError",
]


class AppError(Exception):
    """Base application error."""

    status_code: int = 500

    def __init__(
        self, detail: str = "An unexpected error occurred", *, code: str | None = None
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

    def __init__(self, detail: str = "Conflict"):
        super().__init__(detail)


class UnprocessableError(AppError):
    status_code = 422

    def __init__(self, detail: str = "Unprocessable entity"):
        super().__init__(detail)
