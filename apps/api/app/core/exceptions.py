from __future__ import annotations


class AppError(Exception):
    """Base application error."""
    status_code: int = 500

    def __init__(self, detail: str = "An unexpected error occurred"):
        self.detail = detail
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

    def __init__(self, detail: str = "Unauthorized"):
        super().__init__(detail)


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
