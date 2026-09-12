"""
Windows Hello can register a passkey only if the RP offers RS256.

Apple's platform authenticators (Touch ID / Face ID / iCloud Keychain) create
ES256 (-7) credentials, so a relying party that advertises only ES256 works on
Apple and looks healthy — but Microsoft Windows Hello's TPM-backed keys are
frequently RSA, and it will refuse to register against an RP that does not list
RS256 (-257). We advertise the algorithm set explicitly (see
``WEBAUTHN_SUPPORTED_PUB_KEY_ALGS`` in ``auth.py``) rather than inheriting
py_webauthn's default, precisely so the set can't shrink underneath us on a
library bump. This guard fails the day RS256 stops being offered — i.e. the day
Windows Hello silently breaks — instead of leaving it to a Windows admin to
discover in production.
"""

from __future__ import annotations

import json

from webauthn import generate_registration_options, options_to_json
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.api.v1.auth import WEBAUTHN_SUPPORTED_PUB_KEY_ALGS

# -7 (Apple, most security keys) and -257 (Windows Hello TPM) are the two that
# decide cross-platform reach; both must always be on offer.
ES256 = COSEAlgorithmIdentifier.ECDSA_SHA_256
RS256 = COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256


def test_supported_algs_cover_both_apple_and_windows_hello() -> None:
    assert ES256 in WEBAUTHN_SUPPORTED_PUB_KEY_ALGS, "ES256 (-7) missing — Apple passkeys"
    assert RS256 in WEBAUTHN_SUPPORTED_PUB_KEY_ALGS, "RS256 (-257) missing — Windows Hello"


def test_registration_options_advertise_rs256_to_the_browser() -> None:
    """
    The value that actually reaches ``navigator.credentials.create()`` — the
    JSON ``pubKeyCredParams`` — must carry RS256, using the same constant the
    endpoint passes to ``generate_registration_options``.
    """
    options = generate_registration_options(
        rp_id="admin.example.com",
        rp_name="Melting Moments Admin",
        user_id=b"0123456789abcdef",
        user_name="admin@example.com",
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        supported_pub_key_algs=WEBAUTHN_SUPPORTED_PUB_KEY_ALGS,
    )

    advertised = {p.alg for p in options.pub_key_cred_params}
    assert RS256 in advertised
    assert ES256 in advertised

    # And it survives serialisation to the browser payload.
    payload = json.loads(options_to_json(options))
    algs = {param["alg"] for param in payload["pubKeyCredParams"]}
    assert RS256.value in algs, "Windows Hello (RS256/-257) not offered to the browser"
    assert ES256.value in algs, "Apple passkeys (ES256/-7) not offered to the browser"
