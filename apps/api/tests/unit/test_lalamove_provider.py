"""
Signing, and refusing to trust anything that is not signed.

The webhook receiver is the one endpoint on this API that a stranger can POST
to and have it change an order's status. Everything it accepts has to be
provably from Lalamove, and the check has to survive the awkward part of their
scheme: the signature covers the `data` object as *they* serialised it, so a
verifier that re-encodes the parsed JSON will reject genuine traffic the moment
their formatting differs from ours by a single space.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import settings
from app.services.providers.lalamove_provider import (
    LalamoveClient,
    LalamoveConfig,
    LalamoveError,
    sign,
)
from app.services.providers.lalamove_provider import _unwrap as _parse

KEY = "pk_test_69d70a31a0e00e80d02050750984a7ee"
SECRET = "sk_test_thisisnotarealsecret"
PATH = "/api/v1/webhooks/lalamove"


@pytest.fixture
def client() -> LalamoveClient:
    return LalamoveClient(
        LalamoveConfig(
            key=KEY,
            secret=SECRET,
            env="production",
            market="AE",
            language="en_AE",
            service_type="CAR",
            webhook_path=PATH,
        )
    )


def _payload(body_json: str, *, timestamp: str = "1775027436", key: str = KEY) -> bytes:
    """A webhook shaped and signed exactly the way Lalamove sends one."""
    signature = sign(
        SECRET, timestamp=timestamp, method="POST", path=PATH, body=body_json
    )
    return (
        "{"
        f'"apiKey":"{key}",'
        f'"timestamp":{timestamp},'
        f'"signature":"{signature}",'
        '"eventId":"DFAF4ABA-B8EA-4CD8-9A9E-F0BEDA70C803",'
        '"eventType":"ORDER_STATUS_CHANGED",'
        '"eventVersion":"v3",'
        f'"data":{body_json}'
        "}"
    ).encode()


DATA = (
    '{"order":{"orderId":"3463513590991397204","status":"PICKED_UP",'
    '"previousStatus":"ON_GOING","market":"AE SHJ"},'
    '"updatedAt":"2026-04-01T15:17:00.00Z"}'
)


def test_documented_signature_formula():
    """
    `HmacSHA256(<ts>\\r\\n<VERB>\\r\\n<PATH>\\r\\n\\r\\n<BODY>, secret)`.

    Pinned as a literal rather than recomputed, so a "tidy-up" of the joiner
    fails here instead of silently rejecting every webhook in production.
    """
    expected = sign(
        "secret", timestamp="1650594477", method="POST", path="/hook", body="{}"
    )
    import hashlib
    import hmac

    assert (
        expected
        == hmac.new(
            b"secret", b"1650594477\r\nPOST\r\n/hook\r\n\r\n{}", hashlib.sha256
        ).hexdigest()
    )


def test_a_genuine_webhook_is_accepted(client):
    payload = client.verify_webhook(_payload(DATA))
    assert payload["eventType"] == "ORDER_STATUS_CHANGED"
    assert payload["data"]["order"]["status"] == "PICKED_UP"


def test_their_spacing_does_not_break_the_check(client):
    """
    Lalamove signs the bytes they sent. This one has spaces after every colon,
    which a re-encode from the parsed object would not reproduce — the raw
    substring has to be what gets hashed.
    """
    spaced = json.dumps(json.loads(DATA), indent=2)
    assert client.verify_webhook(_payload(spaced))["data"]["updatedAt"]


def test_a_tampered_status_is_rejected(client):
    """The attack this exists to stop: flipping an order to delivered."""
    good = _payload(DATA).decode()
    forged = good.replace("PICKED_UP", "COMPLETED").encode()
    with pytest.raises(LalamoveError, match="signature"):
        client.verify_webhook(forged)


def test_a_signature_from_another_account_is_rejected(client):
    other = LalamoveClient(
        LalamoveConfig(
            key=KEY,
            secret="sk_test_someoneelsessecret",
            env="production",
            market="AE",
            language="en_AE",
            service_type="CAR",
            webhook_path=PATH,
        )
    )
    with pytest.raises(LalamoveError, match="signature"):
        other.verify_webhook(_payload(DATA))


def test_a_signature_for_another_path_is_rejected(client):
    """
    The path is part of what is signed. A webhook meant for a different
    endpoint of ours must not be replayable against this one.
    """
    elsewhere = sign(
        SECRET, timestamp="1775027436", method="POST", path="/other", body=DATA
    )
    body = (
        f'{{"apiKey":"{KEY}","timestamp":1775027436,"signature":"{elsewhere}",'
        f'"eventId":"E1","eventType":"ORDER_STATUS_CHANGED","data":{DATA}}}'
    ).encode()
    with pytest.raises(LalamoveError, match="signature"):
        client.verify_webhook(body)


def test_a_webhook_for_a_different_api_key_is_rejected(client):
    with pytest.raises(LalamoveError, match="different API key"):
        client.verify_webhook(_payload(DATA, key="pk_prod_someoneelse"))


def test_an_unsigned_webhook_is_rejected(client):
    body = json.dumps({"apiKey": KEY, "timestamp": 1, "data": {}}).encode()
    with pytest.raises(LalamoveError, match="unsigned"):
        client.verify_webhook(body)


def test_junk_is_rejected(client):
    with pytest.raises(LalamoveError):
        client.verify_webhook(b"not json at all")


def test_nothing_is_verified_without_credentials():
    """
    An unconfigured deployment must not accept webhooks. With no secret the
    comparison would be against a signature anyone could compute.
    """
    bare = LalamoveClient(
        LalamoveConfig(
            key="",
            secret="",
            env="production",
            market="AE",
            language="en_AE",
            service_type="CAR",
            webhook_path=PATH,
        )
    )
    assert bare.is_configured is False
    with pytest.raises(LalamoveError, match="not configured"):
        bare.verify_webhook(_payload(DATA))


def test_the_default_webhook_path_is_the_one_we_serve():
    """
    Signing covers the path, so this setting and the route have to agree
    exactly. They live in different files; this is what notices when one moves.

    Read from the OpenAPI schema rather than `app.routes`, for the reason
    `test_pos_subapp` already records: FastAPI 0.140 stopped flattening
    `include_router` into that attribute, so walking it sees only the handful
    of endpoints declared directly on the app and reports every mounted router
    as absent.
    """
    from app.main import app

    assert settings.LALAMOVE_WEBHOOK_PATH in set(app.openapi()["paths"])


# ── error shapes ──────────────────────────────────────────────────────────────


def test_a_bare_error_code_is_read_as_the_id():
    """
    Their quotation endpoint answers `{"message": "ERR_..."}` with no `errors`
    array, and only the array was being read — so `error_id` was None for the
    one call that produces these codes most. Checkout said "Courier quote
    failed" for an address that was simply out of range, and the dispatch sweep
    kept retrying dispatches that could never succeed.
    """
    with pytest.raises(LalamoveError) as raised:
        _parse(httpx.Response(422, json={"message": "ERR_OUT_OF_SERVICE_AREA"}))
    assert raised.value.error_id == "ERR_OUT_OF_SERVICE_AREA"
    assert raised.value.is_out_of_service_area


def test_the_errors_array_still_wins_where_they_send_one():
    with pytest.raises(LalamoveError) as raised:
        _parse(
            httpx.Response(
                402,
                json={
                    "errors": [{"id": "ERR_INSUFFICIENT_CREDIT", "message": "top up"}]
                },
            )
        )
    assert raised.value.error_id == "ERR_INSUFFICIENT_CREDIT"
    assert raised.value.is_insufficient_credit


def test_prose_mentioning_a_code_is_not_an_id():
    """
    Only a bare token counts. A sentence that happens to name a code is prose,
    and treating it as an id would let a reworded error start driving retry
    decisions.
    """
    with pytest.raises(LalamoveError) as raised:
        _parse(
            httpx.Response(
                422, json={"message": "we hit ERR_OUT_OF_SERVICE_AREA on stop 2"}
            )
        )
    assert raised.value.error_id is None
    assert not raised.value.is_out_of_service_area
