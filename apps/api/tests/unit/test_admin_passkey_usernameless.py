"""
Usernameless passkey sign-in on the admin console's first step.

The login page offers "Sign in with a passkey" (and passkey autofill) before an
email is typed. The options call then names no account, and the verify call
finds the account from the credential id. These tests sign real WebAuthn
assertions with a throwaway P-256 key, so they exercise py_webauthn's actual
verification rather than a mock of it.
"""

from __future__ import annotations

import hashlib
import json
import struct
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import bytes_to_base64url

from app.api.v1 import auth
from app.core.exceptions import BadRequestError, UnauthorizedError
from app.models import AdminPasskey, User, WebAuthnChallenge

ORIGIN = "http://localhost:3001"  # settings.ADMIN_URL default
RP_ID = "localhost"


def _cose_public_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    numbers = key.public_key().public_numbers()
    return cbor2.dumps(
        {
            1: 2,  # kty: EC2
            3: -7,  # alg: ES256
            -1: 1,  # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        }
    )


def _assertion(
    key: ec.EllipticCurvePrivateKey,
    *,
    credential_id: str,
    challenge: str,
    user_handle: bytes | None,
    sign_count: int = 1,
) -> dict:
    """A browser's `startAuthentication` result, signed by `key`."""
    client_data = json.dumps(
        {"type": "webauthn.get", "challenge": challenge, "origin": ORIGIN}
    ).encode()
    # rpIdHash | flags (UP|UV) | signCount
    auth_data = (
        hashlib.sha256(RP_ID.encode()).digest()
        + bytes([0x05])
        + struct.pack(">I", sign_count)
    )
    signature = key.sign(
        auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
    )
    response = {
        "clientDataJSON": bytes_to_base64url(client_data),
        "authenticatorData": bytes_to_base64url(auth_data),
        "signature": bytes_to_base64url(signature),
    }
    if user_handle is not None:
        response["userHandle"] = bytes_to_base64url(user_handle)
    return {
        "id": credential_id,
        "rawId": credential_id,
        "type": "public-key",
        "response": response,
        "clientExtensionResults": {},
    }


def _result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.fixture
def world():
    key = ec.generate_private_key(ec.SECP256R1())
    user = User(
        id=uuid.uuid4(),
        email="manager@example.com",
        is_admin=True,
        is_active=True,
    )
    credential_id = bytes_to_base64url(b"credential-0001")
    passkey = AdminPasskey(
        id=uuid.uuid4(),
        user_id=user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(_cose_public_key(key)),
        sign_count=0,
    )
    challenge_value = bytes_to_base64url(b"a-fresh-random-challenge-0123456")
    challenge = WebAuthnChallenge(
        id=uuid.uuid4(),
        user_id=None,
        email="",
        challenge=challenge_value,
        ceremony=auth.DISCOVERABLE_CEREMONY,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db = AsyncMock()
    db.add = MagicMock()
    # verify: passkey by credential id, then the challenge by its value.
    db.execute = AsyncMock(side_effect=[_result(passkey), _result(challenge)])
    db.get = AsyncMock(return_value=user)
    return SimpleNamespace(
        key=key,
        user=user,
        passkey=passkey,
        challenge=challenge,
        credential_id=credential_id,
        db=db,
    )


async def _verify(world, credential: dict):
    token = SimpleNamespace(access_token="a", refresh_token="r")
    with (
        patch.object(auth, "_make_token_response", AsyncMock(return_value=token)),
        patch.object(auth, "_set_auth_cookies", MagicMock()),
    ):
        # The undecorated handler: `@limiter.limit` insists on a real request.
        return await auth.passkey_login_verify.__wrapped__(
            request=MagicMock(),
            response=MagicMock(),
            body=auth.PasskeyLoginVerifyRequest(email=None, credential=credential),
            db=world.db,
        )


async def test_options_without_an_email_name_no_account():
    db = AsyncMock()
    db.add = MagicMock()

    resp = await auth.passkey_login_options.__wrapped__(
        request=MagicMock(),
        body=auth.PasskeyLoginOptionsRequest(email=None),
        db=db,
    )

    assert resp.options.get("allowCredentials", []) == []
    stored = db.add.call_args.args[0]
    assert stored.ceremony == auth.DISCOVERABLE_CEREMONY
    assert stored.user_id is None
    assert stored.challenge == resp.options["challenge"]
    # Only the expiry sweep ran: one browser's challenge never evicts another's.
    assert db.execute.await_count == 1


async def test_a_signed_usernameless_assertion_signs_in_its_owner(world):
    credential = _assertion(
        world.key,
        credential_id=world.credential_id,
        challenge=world.challenge.challenge,
        user_handle=world.user.id.bytes,
        sign_count=7,
    )

    resp = await _verify(world, credential)

    assert resp.access_token == "a"
    assert world.passkey.sign_count == 7
    assert world.passkey.last_used_at is not None
    world.db.delete.assert_awaited_once_with(world.challenge)  # single use


async def test_a_user_handle_naming_another_account_is_refused(world):
    credential = _assertion(
        world.key,
        credential_id=world.credential_id,
        challenge=world.challenge.challenge,
        user_handle=uuid.uuid4().bytes,
    )
    with pytest.raises(UnauthorizedError):
        await _verify(world, credential)


async def test_a_signature_from_another_key_is_refused(world):
    impostor = ec.generate_private_key(ec.SECP256R1())
    credential = _assertion(
        impostor,
        credential_id=world.credential_id,
        challenge=world.challenge.challenge,
        user_handle=world.user.id.bytes,
    )
    with pytest.raises(UnauthorizedError):
        await _verify(world, credential)
    assert world.passkey.sign_count == 0


async def test_an_unknown_passkey_is_refused(world):
    world.db.execute = AsyncMock(return_value=_result(None))
    credential = _assertion(
        world.key,
        credential_id=bytes_to_base64url(b"never-registered"),
        challenge=world.challenge.challenge,
        user_handle=None,
    )
    with pytest.raises(UnauthorizedError, match="isn't registered"):
        await _verify(world, credential)


async def test_a_passkey_of_a_deactivated_admin_is_refused(world):
    world.user.is_active = False
    credential = _assertion(
        world.key,
        credential_id=world.credential_id,
        challenge=world.challenge.challenge,
        user_handle=world.user.id.bytes,
    )
    with pytest.raises(UnauthorizedError):
        await _verify(world, credential)


async def test_an_expired_or_unknown_challenge_is_refused(world):
    world.db.execute = AsyncMock(side_effect=[_result(world.passkey), _result(None)])
    credential = _assertion(
        world.key,
        credential_id=world.credential_id,
        challenge=world.challenge.challenge,
        user_handle=world.user.id.bytes,
    )
    with pytest.raises(BadRequestError, match="expired"):
        await _verify(world, credential)


def test_garbled_client_data_is_a_bad_request():
    with pytest.raises(BadRequestError):
        auth._signed_challenge({"response": {"clientDataJSON": "!!not-base64"}})
    with pytest.raises(BadRequestError):
        auth._signed_challenge({"response": {}})
