"""
What goes on the wire to noon Send.

Three of their conventions are easy to get silently wrong and expensive when
you do: coordinates are integers scaled by ten million, money is fils, and
`cod_value` and `prepaid_value` may not both be present. A float latitude or an
AED amount is a 422 nobody reads until orders stop dispatching, so each one is
pinned here rather than trusted to the caller.

The fourth thing pinned here is which failures mean "try the other courier".
noon Send has no machine-readable error ids, so that decision is made by reading
their prose, and a change in the wording it keys on has to fail loudly.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.providers import noon_send_provider as ns
from app.services.providers.noon_send_provider import (
    NoonSendClient,
    NoonSendConfig,
    NoonSendError,
    coordinate,
    fils,
)

KITCHEN = (25.3304139, 55.3736131)


def _config(**overrides) -> NoonSendConfig:
    values = {
        "api_key": "test-key",
        "env": "staging",
        "locale": "en-ae",
        "client_code": "noon_food",
        "timeout": 5.0,
    }
    values.update(overrides)
    return NoonSendConfig(**values)


class _Recorder:
    """Stands in for httpx.AsyncClient and keeps every request it was given."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[dict] = []

    def __call__(self, **_kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def request(self, method, url, *, json=None, headers=None, **_kwargs):
        self.requests.append(
            {"method": method, "url": url, "json": json, "headers": headers or {}}
        )
        return self.responses.pop(0)


def _response(status: int, payload) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "https://example.test"),
    )


# ── the two encodings ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "degrees,expected",
    [
        (25.3304139, 253304139),
        (55.3736131, 553736131),
        (25.0, 250000000),
        (-0.0000001, -1),
    ],
)
def test_coordinates_are_integers_scaled_by_ten_million(degrees, expected):
    assert coordinate(degrees) == expected
    assert isinstance(coordinate(degrees), int)


@pytest.mark.parametrize(
    "aed,expected",
    [("12.00", 1200), ("12.34", 1234), (0, 0), ("199.99", 19999), ("0.05", 5)],
)
def test_money_is_fils(aed, expected):
    assert fils(aed) == expected
    assert isinstance(fils(aed), int)


# ── the request ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_task_sends_the_headers_and_shape_they_require(monkeypatch):
    recorder = _Recorder(
        [_response(200, {"mp_task_nr": "ABC123", "status": "created"})]
    )
    monkeypatch.setattr(ns.httpx, "AsyncClient", recorder)

    client = NoonSendClient(_config())
    result = await client.create_task(
        order_reference="MM-1001",
        outlet_code="PCKP_TEST123",
        drop_off_address={
            "lat": coordinate(KITCHEN[0]),
            "lng": coordinate(KITCHEN[1]),
            "address": "Garden Tower 1, Al Majaz 3",
            "contact_name": "Hussain",
            "contact_phone_number": "+971501234567",
            "country_code": "ae",
        },
        prepaid_value=fils("185.00"),
        delivery_notes="Order MM-1001",
    )

    assert result == {"mp_task_nr": "ABC123", "status": "created"}
    sent = recorder.requests[0]
    assert sent["url"].endswith("/public/v1/create-task")
    assert sent["headers"]["X-API-Key"] == "test-key"
    assert sent["headers"]["X-Locale"] == "en-ae"
    # Without this, a retried request after a timeout puts a second rider on the
    # same cake.
    assert sent["headers"]["X-Idempotency-Key"] == "MM-1001"
    assert sent["json"]["outlet_code"] == "PCKP_TEST123"
    assert sent["json"]["client_code"] == "noon_food"
    assert sent["json"]["prepaid_value"] == 18500


@pytest.mark.asyncio
async def test_a_cash_order_sends_cod_and_never_both(monkeypatch):
    recorder = _Recorder([_response(200, {"mp_task_nr": "X", "status": "created"})])
    monkeypatch.setattr(ns.httpx, "AsyncClient", recorder)

    await NoonSendClient(_config()).create_task(
        order_reference="MM-1002",
        outlet_code="PCKP_TEST123",
        drop_off_address={},
        cod_value=fils("185.00"),
        prepaid_value=fils("185.00"),
    )

    body = recorder.requests[0]["json"]
    assert body["cod_value"] == 18500
    # Sending both is a 422 even when one of them is zero.
    assert "prepaid_value" not in body


@pytest.mark.asyncio
async def test_nothing_is_sent_for_an_unregistered_branch(monkeypatch):
    """
    A branch with no outlet code has nowhere for a rider to collect from, and
    the request is refused here rather than sent and rejected. The message names
    the branch rather than the account, because the fix is a field in the admin.
    """
    recorder = _Recorder([])
    monkeypatch.setattr(ns.httpx, "AsyncClient", recorder)

    with pytest.raises(NoonSendError, match="not registered with noon Send"):
        await NoonSendClient(_config()).create_task(
            order_reference="MM-1003", outlet_code="", drop_off_address={}
        )
    assert recorder.requests == []


# ── retries and failures ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_server_error_is_retried_once(monkeypatch):
    recorder = _Recorder(
        [
            _response(503, {"detail": "upstream unavailable"}),
            _response(200, {"mp_task_nr": "ABC", "status": "created"}),
        ]
    )
    monkeypatch.setattr(ns.httpx, "AsyncClient", recorder)

    result = await NoonSendClient(_config()).create_task(
        order_reference="MM-1004", outlet_code="PCKP_TEST123", drop_off_address={}
    )
    assert result["mp_task_nr"] == "ABC"
    assert len(recorder.requests) == 2


@pytest.mark.asyncio
async def test_a_validation_error_is_flattened_into_one_sentence(monkeypatch):
    """
    The only reader of this string is an admin looking at `last_error`, so a
    nested FastAPI validation payload has to come out as prose.
    """
    recorder = _Recorder(
        [
            _response(
                422,
                {
                    "detail": [
                        {
                            "loc": ["body", "drop_off_address", "lat"],
                            "msg": "value is not a valid integer",
                        }
                    ]
                },
            )
        ]
    )
    monkeypatch.setattr(ns.httpx, "AsyncClient", recorder)

    with pytest.raises(NoonSendError) as caught:
        await NoonSendClient(_config()).create_task(
            order_reference="MM-1005", outlet_code="PCKP_TEST123", drop_off_address={}
        )
    assert "drop_off_address.lat: value is not a valid integer" in str(caught.value)


@pytest.mark.parametrize(
    "status,message",
    [
        (422, "distance between pickup and drop-off exceeds 15 km"),
        (400, "location is not serviceable"),
        (404, "outlet not found"),
        (409, "no rider available"),
        (200, "drop-off is outside the fleet area"),
    ],
)
def test_a_refusal_to_carry_is_told_apart_from_a_breakage(status, message):
    """These send the order to Lalamove instead of leaving it stranded."""
    assert NoonSendError(message, status=status).is_unserviceable


@pytest.mark.parametrize(
    "status,message",
    [
        (500, "internal server error"),
        (502, "bad gateway"),
        (None, "noon Send is unreachable: timed out"),
    ],
)
def test_a_breakage_is_not_mistaken_for_a_refusal(status, message):
    """Retrying is the right answer here; swapping courier is not."""
    assert not NoonSendError(message, status=status).is_unserviceable
