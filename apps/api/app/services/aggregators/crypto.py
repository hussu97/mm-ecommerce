"""The Fernet envelope around a marketplace session at rest.

A bootstrap logs in to a portal and captures cookies, tokens and the header
fingerprint that make its private API answer. That bundle is a live credential —
anyone holding it can read the shop's sales and, on some channels, act — so it
never sits in the database as plaintext. It is Fernet-sealed under
`AGGREGATOR_CONFIG_ENCRYPTION_KEY` (the same idiom `mm-aggregator-automation`
used), and an unset key means the ingest simply cannot store or read a session
rather than falling back to holding one in the clear.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class AggregatorCryptoError(RuntimeError):
    """The session store cannot encrypt or decrypt — a config or key fault.

    Raised for a missing/invalid key and for a blob that will not decrypt (a
    rotated key, a truncated value). Never swallowed into "no session": a
    session that exists but cannot be read is a fault to surface, not a silent
    re-bootstrap that would mint a second live credential.
    """


def is_configured() -> bool:
    """Whether a key is present at all — the gate the ingest checks before running."""
    return bool(settings.AGGREGATOR_CONFIG_ENCRYPTION_KEY)


def _fernet() -> Fernet:
    key = settings.AGGREGATOR_CONFIG_ENCRYPTION_KEY
    if not key:
        raise AggregatorCryptoError(
            "AGGREGATOR_CONFIG_ENCRYPTION_KEY is unset; refusing to handle an "
            "aggregator session without one"
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise AggregatorCryptoError(
            "AGGREGATOR_CONFIG_ENCRYPTION_KEY is not a valid Fernet key "
            "(expected a 32-byte urlsafe-base64 value)"
        ) from exc


def encrypt_json(value: dict[str, Any] | None) -> str | None:
    """Seal a JSON-able mapping, or pass None through untouched."""
    if value is None:
        return None
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode()
    return _fernet().encrypt(raw).decode()


def decrypt_json(blob: str | None) -> dict[str, Any] | None:
    """Open a sealed blob back to its mapping, or None for an empty column."""
    if not blob:
        return None
    try:
        opened = _fernet().decrypt(blob.encode())
    except InvalidToken as exc:
        raise AggregatorCryptoError(
            "could not decrypt an aggregator session blob — the encryption key "
            "may have rotated since it was stored"
        ) from exc
    return json.loads(opened.decode())
