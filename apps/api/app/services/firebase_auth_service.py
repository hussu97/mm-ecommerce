"""
Proof that somebody holds the phone number they typed.

Firebase does the sending and the code-checking; this verifies the receipt. The
browser runs the OTP flow against Firebase's own endpoint and comes away with an
ID token — a short-lived RS256 JWT saying "this project confirmed this phone" —
and the only thing worth trusting on the server is that token's signature.

**There is no secret here, deliberately.** A Firebase ID token is verified
against Google's published certificates, so the server needs the project id and
nothing else: no service-account key to store, rotate, leak, or forget to put in
the fifth of five deploy files. The Admin SDK exists for minting tokens and
privileged reads, and we do neither.

The checks are the standard four, and every one of them matters:

  * **signature**, against the cert named by the token's `kid`. Without it a
    token is a JSON object anybody can type.
  * **`aud` is our project.** A token minted by any other Firebase project is a
    perfectly valid token — signed by the same Google key — and would otherwise
    verify. Anyone can create a project.
  * **`iss` is `securetoken.google.com/<project>`**, which is what distinguishes
    an ID token from the other things Google signs.
  * **`exp`/`iat`**, so a token cannot be replayed a week later.

Unlike the Turnstile check, an unreachable verifier here **fails closed**. That
check protects a signup form and letting an hour of it through is recoverable;
this one is the only thing standing between "typed a number" and "owns the
number", and it gates a discount. A verifier we cannot reach is a verification
that did not happen.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from jose import jwt
from jose.exceptions import JOSEError

from app.core.config import settings
from app.core.phone import normalise_phone

logger = logging.getLogger(__name__)

__all__ = [
    "CERT_URL",
    "PhoneVerification",
    "VerificationError",
    "is_enabled",
    "is_phone_verified",
    "record_verification",
    "verify_id_token",
]

#: Google's public signing certificates for Firebase ID tokens. Public, keyless,
#: and rotated by Google — which is why the client below caches by `kid` rather
#: than pinning anything.
CERT_URL = "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"

#: Some slack for clock skew between us and Google. Small: this is the only
#: thing keeping an expired token expired.
LEEWAY_SECONDS = 30


class VerificationError(Exception):
    """The token is not acceptable. The message is for the log, not the caller."""


class PhoneVerification:
    """A verified phone number and the token it came from."""

    __slots__ = ("phone", "firebase_uid", "verified_at")

    def __init__(self, phone: str, firebase_uid: str, verified_at: int) -> None:
        self.phone = phone
        self.firebase_uid = firebase_uid
        self.verified_at = verified_at

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<PhoneVerification {self.phone}>"


def is_enabled() -> bool:
    """
    Whether phone verification is configured at all.

    Without a project id there is nothing to verify a token *against*, so the
    callers treat it the way they treat an unconfigured courier: the feature
    that depends on it is off, rather than open.
    """
    return bool((settings.FIREBASE_PROJECT_ID or "").strip())


#: Google's keys, cached by `kid`, with the time they were fetched.
#:
#: Cached because the certificates change a few times a year and a verification
#: happens on every address a customer saves; fetched again on an unknown `kid`
#: because that is exactly what a rotation looks like from here. Both halves are
#: needed — a cache with no refresh turns a routine rotation into an outage, and
#: a refresh with no cache puts a network round trip in front of every checkout.
_keys: dict[str, dict] = {}
_fetched_at: float = 0.0

#: How long to trust the cached set before refetching, in seconds.
_CACHE_SECONDS = 3600

#: The floor between two fetches of Google's certificates.
#:
#: An unknown `kid` triggers a refetch, because that is what a key rotation
#: looks like from here — and `kid` is a field of an unauthenticated, attacker-
#: supplied token. Without a floor, a stream of tokens carrying random `kid`s
#: turns our verification endpoint into an amplifier pointed at Google, one
#: outbound HTTPS request per inbound one. The endpoint's own rate limit caps
#: the rate; this caps the amplification.
#:
#: Well under a real rotation's overlap window, so a genuine rotation still
#: resolves within a minute.
_MIN_REFETCH_SECONDS = 60

#: When we last *attempted* a fetch, successful or not. Distinct from
#: `_fetched_at`, which is when we last succeeded: a failing endpoint must not
#: be retried on every request either.
_attempted_at: float = 0.0


def reset_keys() -> None:
    """Drop the cached certificates. For tests, and for a rotation we got wrong."""
    global _keys, _fetched_at, _attempted_at
    _keys = {}
    _fetched_at = 0.0
    _attempted_at = 0.0


async def _fetch_keys() -> dict[str, dict]:
    async with httpx.AsyncClient(timeout=settings.FIREBASE_TIMEOUT_SECONDS) as client:
        payload = (await client.get(CERT_URL)).json()
    return {key["kid"]: key for key in payload.get("keys", []) if key.get("kid")}


async def _signing_key(kid: str) -> dict:
    """The key this token was signed with, refetching if we have not seen it."""
    global _keys, _fetched_at, _attempted_at

    now = time.time()
    stale = (now - _fetched_at) > _CACHE_SECONDS
    # An unknown `kid` is worth one fetch, not one fetch per request: see
    # `_MIN_REFETCH_SECONDS`.
    #
    # The floor does not apply while we hold no keys at all. That is a cold
    # start, or a fetch that failed, and in both cases every verification is
    # failing anyway — throttling the one call that would fix it would turn a
    # blip in Google's endpoint into a minute of refusals.
    may_refetch = not _keys or (now - _attempted_at) >= _MIN_REFETCH_SECONDS
    if (kid not in _keys or stale) and may_refetch:
        _attempted_at = now
        _keys = await _fetch_keys()
        _fetched_at = time.time()

    if kid not in _keys:
        # Signed by something Google is not currently publishing. Either a
        # forgery or a key withdrawn mid-flight; neither is acceptable.
        raise VerificationError(f"unknown signing key {kid!r}")
    return _keys[kid]


def _normalise(phone: str) -> str:
    """
    E.164, whichever end the number came from.

    Firebase issues `phone_number` in E.164 already, so on the write side this
    is a guard rather than a conversion. The read side is where it earns its
    keep: `is_phone_verified` is asked about the number typed into a checkout
    form, and `0501234567` has to find the ledger row Firebase wrote as
    `+971501234567` or the customer verifies their phone and is told they have
    not.

    Shared with the coupon rule and the courier booking rather than reimplemented
    — one definition of "the same number", or the surfaces disagree.
    """
    return normalise_phone(phone) or "".join((phone or "").split())


async def verify_id_token(token: str | None) -> PhoneVerification:
    """
    The phone number this token proves, or `VerificationError`.

    Raises rather than returning a flag: there is no useful partial answer, and
    a caller that forgets to check a boolean fails open.
    """
    if not is_enabled():
        raise VerificationError("phone verification is not configured")
    if not token or not token.strip():
        raise VerificationError("no token supplied")

    project = settings.FIREBASE_PROJECT_ID.strip()

    try:
        header = jwt.get_unverified_header(token)
    except JOSEError as exc:
        raise VerificationError(f"unreadable token: {exc}") from exc

    if header.get("alg") != "RS256":
        # `alg` comes from the token, so an attacker picks it. `none` and the
        # HMAC family are the classic confusion attacks; pinning the one
        # algorithm Firebase actually uses closes both.
        raise VerificationError(f"unexpected algorithm {header.get('alg')!r}")

    kid = header.get("kid")
    if not kid:
        raise VerificationError("token names no signing key")

    try:
        key = await _signing_key(kid)
    except VerificationError:
        raise
    except Exception as exc:
        # Includes the network being down. Fails closed on purpose — see the
        # module docstring.
        raise VerificationError(f"could not fetch signing key: {exc}") from exc

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=project,
            issuer=f"https://securetoken.google.com/{project}",
            options={
                "leeway": LEEWAY_SECONDS,
                "require_exp": True,
                "require_iat": True,
                "require_aud": True,
                "require_iss": True,
                "require_sub": True,
            },
        )
    except JOSEError as exc:
        raise VerificationError(f"token rejected: {exc}") from exc

    # `auth_time` is when the user actually proved the number, as opposed to
    # `iat` which moves every time the SDK refreshes the token. A token minted
    # from a months-old session would otherwise look freshly verified.
    auth_time = int(claims.get("auth_time") or claims.get("iat") or 0)
    max_age = settings.FIREBASE_MAX_AUTH_AGE_SECONDS
    if max_age and auth_time and (time.time() - auth_time) > max_age:
        raise VerificationError("the verification is too old to reuse")

    phone = claims.get("phone_number")
    if not phone:
        # A real Firebase token, correctly signed, for a sign-in method that
        # proves nothing about a phone — email link, Google, anonymous. It must
        # not be accepted as phone proof.
        raise VerificationError("token carries no phone number")

    return PhoneVerification(
        phone=_normalise(str(phone)),
        firebase_uid=str(claims["sub"]),
        verified_at=auth_time,
    )


async def record_verification(
    db,
    proof: PhoneVerification,
    user_id=None,
):
    """Write the proof down, and mark the account if there is one."""
    from datetime import datetime, timezone

    from app.models.phone_verification import (
        PhoneVerification as PhoneVerificationRow,
    )
    from app.models.user import User

    at = datetime.fromtimestamp(proof.verified_at or 0, tz=timezone.utc)
    row = PhoneVerificationRow(
        phone=proof.phone,
        firebase_uid=proof.firebase_uid,
        verified_at=at,
        user_id=user_id,
    )
    db.add(row)

    if user_id is not None:
        user = await db.get(User, user_id)
        if user is not None:
            # The number that was proved, not the one already on file. Someone
            # verifying a new handset is telling us their number changed.
            user.phone = proof.phone
            user.phone_verified_at = at
    await db.flush()
    return row


async def is_phone_verified(db, phone: str | None) -> bool:
    """
    Whether this number has been proved recently enough to still mean something.

    Reads the ledger rather than the account, because the customer this gates is
    usually a guest with no account at all. `FIREBASE_MAX_AUTH_AGE_SECONDS` is
    about a single token's freshness; this window is about how long a proof
    stays good for, and they are deliberately different questions — a customer
    should not have to re-verify between adding an address and paying.
    """
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from app.models.phone_verification import (
        PhoneVerification as PhoneVerificationRow,
    )

    if not phone or not phone.strip():
        return False

    cutoff = datetime.now(timezone.utc) - timedelta(
        seconds=settings.PHONE_VERIFICATION_TTL_SECONDS
    )
    result = await db.execute(
        select(PhoneVerificationRow.id)
        .where(
            PhoneVerificationRow.phone == _normalise(phone),
            PhoneVerificationRow.verified_at >= cutoff,
        )
        .limit(1)
    )
    return result.scalar() is not None


async def certificates_reachable() -> bool:
    """A health probe, so a misconfigured deploy is visible before a customer
    finds it."""
    try:
        async with httpx.AsyncClient(
            timeout=settings.FIREBASE_TIMEOUT_SECONDS
        ) as client:
            return (await client.get(CERT_URL)).status_code == 200
    except Exception:  # pragma: no cover - network
        return False
