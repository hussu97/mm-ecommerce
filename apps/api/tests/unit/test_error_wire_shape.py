"""
The one error dialect, on the wire.

The routers used to speak two: `AppError` subclasses rendered by the shared
handler in `app_setup.configure`, and bare `HTTPException` rendered by
FastAPI's default. Both produce `{"detail": <string-or-object>}` — which is
exactly why converting the holdouts (P2-8) was safe, and exactly what this
test pins so it stays safe: the admin's `ApiError` parses `detail` as
string-or-object, and a handler change that moved the payload elsewhere would
break every client while looking like a refactor.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.app_setup import configure
from app.core.exceptions import (
    BadGatewayError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
)

#: The shape the Lalamove re-quote 409 sends: a message plus the fresh quote,
#: inside `detail`, for the dialog to offer rather than only apologise.
_QUOTE_BODY = {
    "message": "The quoted price has expired.",
    "quote": {"quotation_id": "q1", "cost": 12.5},
}


def _client() -> TestClient:
    app = FastAPI()
    configure(app, allowed_hosts=["*"], cors_origins=[])

    @app.get("/old-404")
    def old_404():
        raise HTTPException(404, "Order 'X' not found")

    @app.get("/new-404")
    def new_404():
        raise NotFoundError("Order 'X' not found")

    @app.get("/old-409-structured")
    def old_409():
        raise HTTPException(409, detail=_QUOTE_BODY)

    @app.get("/new-409-structured")
    def new_409():
        raise ConflictError("The quoted price has expired.", payload=_QUOTE_BODY)

    @app.get("/new-502")
    def new_502():
        raise BadGatewayError("No price available")

    @app.get("/new-503")
    def new_503():
        raise ServiceUnavailableError("The courier is not configured")

    return TestClient(app, raise_server_exceptions=False)


def test_a_plain_app_error_is_byte_identical_to_the_http_exception_it_replaced():
    client = _client()
    old, new = client.get("/old-404"), client.get("/new-404")
    assert (old.status_code, old.content) == (404, new.content)
    assert new.status_code == 404


def test_a_payload_takes_the_detail_slot_exactly_as_a_dict_detail_did():
    """`AppError.payload` exists for the structured 409s; the payload must land
    where `HTTPException(detail=<dict>)` put it, byte for byte."""
    client = _client()
    old = client.get("/old-409-structured")
    new = client.get("/new-409-structured")
    assert (old.status_code, old.content) == (409, new.content)
    assert new.status_code == 409
    assert new.json()["detail"] == _QUOTE_BODY


def test_the_gateway_statuses_carry_the_same_single_key_body():
    """502/503 were the last excuse for a bare `HTTPException` in the order
    routers; their subclasses answer with the same one-key shape."""
    client = _client()
    for path, code in (("/new-502", 502), ("/new-503", 503)):
        response = client.get(path)
        assert response.status_code == code
        assert set(response.json()) == {"detail"}
