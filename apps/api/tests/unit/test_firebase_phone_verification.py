"""
What the phone-verification check must refuse.

The happy path is uninteresting — Google signs a token, we check the signature.
Everything worth testing is a token that is *almost* acceptable, because those
are the ones a naive implementation lets through:

  * a token from somebody else's Firebase project, signed by the same Google
    key, valid in every respect except that it is not ours;
  * a Firebase token for a sign-in method that proves nothing about a phone;
  * `alg: none`, and the RS256/HMAC confusion that has broken JWT libraries for
    a decade;
  * a token signed by a key Google is not publishing.

And one thing it must refuse to *fail open* on: an unreachable certificate
endpoint. The Turnstile check treats an unreachable verifier as a pass, because
an hour of unchecked signups is recoverable. This one gates a discount, and a
verifier we cannot reach is a verification that did not happen.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.services import firebase_auth_service as fb

PROJECT = "melting-moments-cakes"
OTHER_PROJECT = "someone-elses-project"
KID = "test-key-1"

# A throwaway RSA pair, generated rather than committed so this file carries no
# key material anyone could mistake for a real one.
#
# The imports above are deliberately not guarded. `cryptography` is a hard
# dependency — python-jose pulls it in — so an ImportError is a broken
# environment and should say so loudly. They were briefly wrapped in a
# try/except, and a typo in an enum name turned this whole file into a silent
# skip: ten security assertions reported as "1 skipped" and nobody the wiser.

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _key.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption(),
).decode()
PUBLIC_PEM = (
    _key.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode()
)


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "FIREBASE_PROJECT_ID", PROJECT)
    monkeypatch.setattr(cfg.settings, "FIREBASE_MAX_AUTH_AGE_SECONDS", 3600)
    fb.reset_keys()
    yield
    fb.reset_keys()


def _token(**over):
    now = int(time.time())
    claims = {
        "iss": f"https://securetoken.google.com/{PROJECT}",
        "aud": PROJECT,
        "sub": "firebase-uid-123",
        "iat": now,
        "exp": now + 3600,
        "auth_time": now,
        "phone_number": "+971501234567",
    }
    claims.update(over)
    headers = {"kid": KID}
    if "alg" in over:
        headers["alg"] = over.pop("alg")
    return jwt.encode(claims, PRIVATE_PEM, algorithm="RS256", headers=headers)


def _serving_our_key():
    """Patch the key lookup to hand back our test public key."""
    return patch.object(fb, "_signing_key", new=AsyncMock(return_value=PUBLIC_PEM))


# ── the one that should work ──────────────────────────────────────────────────


async def test_a_good_token_yields_the_phone_number():
    with _serving_our_key():
        proof = await fb.verify_id_token(_token())
    assert proof.phone == "+971501234567"
    assert proof.firebase_uid == "firebase-uid-123"


# ── the near-misses ───────────────────────────────────────────────────────────


async def test_a_token_from_another_firebase_project_is_refused():
    """
    The one that matters most. Anyone can create a Firebase project in a minute;
    tokens from it are signed by the same Google keys and are valid in every
    respect except `aud`. Without the audience check, a stranger mints proof
    that they own any number they like.
    """
    token = _token(
        aud=OTHER_PROJECT, iss=f"https://securetoken.google.com/{OTHER_PROJECT}"
    )
    with _serving_our_key(), pytest.raises(fb.VerificationError):
        await fb.verify_id_token(token)


async def test_a_token_with_no_phone_claim_is_refused():
    """A real, correctly signed Firebase token for an email or Google sign-in
    proves nothing about a phone and must not be accepted as if it did."""
    token = _token()
    # Re-mint without the claim.
    import json
    from base64 import urlsafe_b64decode

    payload = json.loads(urlsafe_b64decode(token.split(".")[1] + "=="))
    payload.pop("phone_number")
    token = jwt.encode(payload, PRIVATE_PEM, algorithm="RS256", headers={"kid": KID})

    with _serving_our_key(), pytest.raises(fb.VerificationError, match="no phone"):
        await fb.verify_id_token(token)


async def test_an_expired_token_is_refused():
    now = int(time.time())
    token = _token(exp=now - 60, iat=now - 3600)
    with _serving_our_key(), pytest.raises(fb.VerificationError):
        await fb.verify_id_token(token)


async def test_a_stale_verification_is_refused():
    """`auth_time` is when the number was actually proved. ID tokens refresh
    hourly and carry it forward, so without this a months-old session presents
    as a fresh verification."""
    now = int(time.time())
    token = _token(auth_time=now - 86400)
    with _serving_our_key(), pytest.raises(fb.VerificationError, match="too old"):
        await fb.verify_id_token(token)


async def test_an_unsigned_token_is_refused():
    """`alg: none` — the oldest JWT attack there is, and the reason the
    algorithm is pinned rather than read from the token."""
    import json
    from base64 import urlsafe_b64encode

    def seg(obj):
        return urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()

    now = int(time.time())
    forged = ".".join(
        [
            seg({"alg": "none", "kid": KID}),
            seg(
                {
                    "iss": f"https://securetoken.google.com/{PROJECT}",
                    "aud": PROJECT,
                    "sub": "attacker",
                    "iat": now,
                    "exp": now + 3600,
                    "phone_number": "+971500000000",
                }
            ),
            "",
        ]
    )
    with _serving_our_key(), pytest.raises(fb.VerificationError):
        await fb.verify_id_token(forged)


async def test_a_token_signed_by_an_unpublished_key_is_refused():
    """Either a forgery or a key withdrawn mid-flight. Neither is acceptable,
    and both look the same from here."""
    with (
        patch.object(fb, "_fetch_keys", new=AsyncMock(return_value={})),
        pytest.raises(fb.VerificationError, match="unknown signing key"),
    ):
        await fb.verify_id_token(_token())


# ── failing closed ────────────────────────────────────────────────────────────


async def test_an_unreachable_certificate_endpoint_refuses_rather_than_passes():
    """
    The deliberate difference from the Turnstile check, which treats an
    unreachable verifier as a pass. That one guards a signup form; this one is
    the only thing between "typed a number" and "owns the number".
    """
    with (
        patch.object(fb, "_fetch_keys", new=AsyncMock(side_effect=OSError("no dns"))),
        pytest.raises(fb.VerificationError),
    ):
        await fb.verify_id_token(_token())


async def test_an_unconfigured_deployment_refuses_rather_than_passes(monkeypatch):
    import app.core.config as cfg

    monkeypatch.setattr(cfg.settings, "FIREBASE_PROJECT_ID", "")
    assert fb.is_enabled() is False
    with pytest.raises(fb.VerificationError, match="not configured"):
        await fb.verify_id_token(_token())


async def test_no_token_at_all_is_refused():
    for empty in (None, "", "   "):
        with pytest.raises(fb.VerificationError):
            await fb.verify_id_token(empty)
