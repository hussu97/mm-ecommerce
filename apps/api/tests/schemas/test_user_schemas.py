from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.user import UserCreate, LoginRequest, PasswordResetConfirm


class TestUserCreate:
    def test_valid_user(self):
        user = UserCreate(
            email="test@example.com",
            password="password123",
        )
        assert user.email == "test@example.com"

    def test_invalid_email(self):
        with pytest.raises(ValidationError):
            UserCreate(
                email="not-an-email",
                password="password123",
            )

    def test_password_too_short(self):
        with pytest.raises(ValidationError):
            UserCreate(
                email="test@example.com",
                password="short",
            )

    def test_password_exactly_8_chars_ok(self):
        user = UserCreate(
            email="test@example.com",
            password="12345678",
        )
        assert len(user.password) == 8

    def test_phone_optional(self):
        user = UserCreate(
            email="test@example.com",
            password="password123",
        )
        assert user.phone is None


class TestLoginRequest:
    def test_valid_login(self):
        req = LoginRequest(email="user@example.com", password="any")
        assert req.email == "user@example.com"

    def test_invalid_email(self):
        with pytest.raises(ValidationError):
            LoginRequest(email="bad", password="any")


class TestPasswordResetConfirm:
    def test_valid(self):
        req = PasswordResetConfirm(token="sometoken", new_password="newpass123")
        assert req.token == "sometoken"

    def test_new_password_too_short(self):
        with pytest.raises(ValidationError):
            PasswordResetConfirm(token="t", new_password="short")
