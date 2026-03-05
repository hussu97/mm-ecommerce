from __future__ import annotations


from app.core.exceptions import (
    AppError,
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    UnprocessableError,
)


class TestStatusCodes:
    def test_app_error_default_500(self):
        assert AppError.status_code == 500

    def test_not_found_404(self):
        assert NotFoundError.status_code == 404

    def test_bad_request_400(self):
        assert BadRequestError.status_code == 400

    def test_unauthorized_401(self):
        assert UnauthorizedError.status_code == 401

    def test_forbidden_403(self):
        assert ForbiddenError.status_code == 403

    def test_conflict_409(self):
        assert ConflictError.status_code == 409

    def test_unprocessable_422(self):
        assert UnprocessableError.status_code == 422


class TestInheritance:
    def test_all_subclass_app_error(self):
        for cls in (
            NotFoundError,
            BadRequestError,
            UnauthorizedError,
            ForbiddenError,
            ConflictError,
            UnprocessableError,
        ):
            assert issubclass(cls, AppError)

    def test_app_error_is_exception(self):
        assert issubclass(AppError, Exception)


class TestMessages:
    def test_default_message(self):
        e = AppError()
        assert e.detail

    def test_custom_message(self):
        e = NotFoundError("Product not found")
        assert e.detail == "Product not found"

    def test_str_equals_detail(self):
        e = BadRequestError("Invalid input")
        assert str(e) == "Invalid input"

    def test_default_not_found_message(self):
        e = NotFoundError()
        assert "not found" in e.detail.lower()
