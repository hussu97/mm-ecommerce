from __future__ import annotations

import pytest
from jose import JWTError

from app.core.security import (
    create_access_token,
    create_password_reset_token,
    create_refresh_token,
    decode_password_reset_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_returns_string(self):
        h = hash_password("mysecret")
        assert isinstance(h, str)

    def test_hash_not_plaintext(self):
        h = hash_password("mysecret")
        assert h != "mysecret"

    def test_verify_correct_password(self):
        h = hash_password("correct")
        assert verify_password("correct", h) is True

    def test_verify_wrong_password(self):
        h = hash_password("correct")
        assert verify_password("wrong", h) is False

    def test_same_password_produces_different_hashes(self):
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # bcrypt salts each hash


class TestAccessToken:
    def test_create_returns_string(self):
        token = create_access_token("uid-1", "user@example.com")
        assert isinstance(token, str)

    def test_decode_payload_fields(self):
        token = create_access_token("uid-1", "user@example.com", is_admin=True)
        payload = decode_token(token)
        assert payload["sub"] == "uid-1"
        assert payload["email"] == "user@example.com"
        assert payload["is_admin"] is True
        assert payload["type"] == "access"

    def test_is_guest_defaults_false(self):
        token = create_access_token("uid-1", "user@example.com")
        payload = decode_token(token)
        assert payload["is_guest"] is False

    def test_decode_invalid_token_raises(self):
        with pytest.raises(JWTError):
            decode_token("not.a.valid.jwt")

    def test_decode_tampered_token_raises(self):
        token = create_access_token("uid-1", "user@example.com")
        with pytest.raises(JWTError):
            decode_token(token + "x")


class TestPasswordResetToken:
    def test_create_and_decode(self):
        token = create_password_reset_token("uid-1", "user@example.com")
        payload = decode_password_reset_token(token)
        assert payload["sub"] == "uid-1"
        assert payload["type"] == "reset"

    def test_access_token_rejected_as_reset(self):
        access_token = create_access_token("uid-1", "user@example.com")
        with pytest.raises(JWTError):
            decode_password_reset_token(access_token)

    def test_reset_token_email_preserved(self):
        token = create_password_reset_token("uid-1", "user@example.com")
        payload = decode_password_reset_token(token)
        assert payload["email"] == "user@example.com"


class TestRefreshToken:
    def test_returns_tuple_of_strings(self):
        raw, h = create_refresh_token()
        assert isinstance(raw, str)
        assert isinstance(h, str)

    def test_raw_and_hash_differ(self):
        raw, h = create_refresh_token()
        assert raw != h

    def test_unique_across_calls(self):
        raw1, _ = create_refresh_token()
        raw2, _ = create_refresh_token()
        assert raw1 != raw2

    def test_hash_is_hex_string(self):
        _, h = create_refresh_token()
        int(h, 16)  # raises ValueError if not valid hex
