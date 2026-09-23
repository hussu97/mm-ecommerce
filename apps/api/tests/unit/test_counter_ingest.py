"""
Local-first counter sale sync, without a database.

The booking itself — replay, conflict, quarantine, mismatch, unknown bundle,
late till and day, inactive product, bad attestation, timestamps, the 40-char
order number, concurrent check numbers and inventory depletion — runs against a
real Postgres in `tests/integration/test_counter_ingest.py`. These pin what the
register depends on at the HTTP edge and the pure helpers:

* 426 below the minimum build, decided before the body is validated;
* 409 carries `code=counter_sale_conflict`; 202/200/201 pass through;
* the replay fingerprint ignores print results;
* the till close / open-with-handover requests stay valid without
  `device_pending_sales`, and the guard refuses a cashier but not a manager.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.devices import get_current_device
from app.core.deps import get_db
from app.core.exceptions import ConflictError
from app.core.pos_builds import COUNTER_LOCAL_FIRST_MIN_BUILD
from app.pos_main import app as pos_app
from app.schemas.pos.tills import TillCloseRequest, TillOpenRequest
from app.schemas.pos_counter import CounterSaleRequest, CounterSaleResponse
from app.services.pos import counter_ingest_service, till_service

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def _sale(**overrides) -> CounterSaleRequest:
    data = {
        "id": str(uuid.uuid4()),
        "branch_id": str(uuid.uuid4()),
        "device_id": str(uuid.uuid4()),
        "till_id": str(uuid.uuid4()),
        "cashier_id": str(uuid.uuid4()),
        "ticket_prefix": "T1",
        "ticket_seq": 42,
        "display_number": "T1-0042",
        "business_date": "2026-09-23",
        "opened_at": (NOW - timedelta(minutes=3)).isoformat(),
        "priced_at": (NOW - timedelta(minutes=1)).isoformat(),
        "closed_at": NOW.isoformat(),
        "bundle_hash": "a" * 64,
        "engine_version": 1,
        "lines": [
            {
                "id": str(uuid.uuid4()),
                "product_id": str(uuid.uuid4()),
                "quantity": 1,
                "totals": {
                    "base_price": "21.00",
                    "options_price": "0.00",
                    "unit_price": "21.00",
                    "gross": "21.00",
                    "discount": "0.00",
                    "total_price": "21.00",
                    "tax_amount": "1.00",
                    "tax_exclusive_total": "20.00",
                    "tax_exclusive_unit": "20.00",
                },
            }
        ],
        "tenders": [
            {
                "id": str(uuid.uuid4()),
                "idempotency_key": "k" * 32,
                "payment_method_id": str(uuid.uuid4()),
                "amount": "21.00",
                "taken_at": NOW.isoformat(),
            }
        ],
        "totals": {
            "subtotal": "21.00",
            "discount_total": "0.00",
            "tax_total": "1.00",
            "total_excl_tax": "20.00",
            "rounding": "0.00",
            "total": "21.00",
        },
        "kitchen_tickets": [
            {
                "sequence": 1,
                "line_ids": [],
                "sent_at": NOW.isoformat(),
                "printed_at": NOW.isoformat(),
            }
        ],
    }
    data["kitchen_tickets"][0]["line_ids"] = [data["lines"][0]["id"]]
    data.update(overrides)
    return CounterSaleRequest.model_validate(data)


# ─── Fingerprint ──────────────────────────────────────────────────────────────


def test_the_fingerprint_ignores_print_results_and_the_retrying_build():
    sale = _sale()
    later = sale.model_copy(deep=True)
    later.receipt_printed_at = NOW + timedelta(seconds=5)
    later.kitchen_tickets[0].printed_at = NOW + timedelta(seconds=9)
    later.app_build = "2000"
    later.clock_offset_ms = 1200
    assert counter_ingest_service.fingerprint(
        later
    ) == counter_ingest_service.fingerprint(sale)


def test_the_fingerprint_sees_any_change_to_the_sale():
    sale = _sale()
    for change in (
        {"customer_name": "X"},
        {"ticket_seq": 43, "display_number": "T1-0043"},
        {"coupon_promotion_id": uuid.uuid4()},
    ):
        assert counter_ingest_service.fingerprint(
            sale.model_copy(update=change)
        ) != counter_ingest_service.fingerprint(sale)


def test_naive_timestamps_are_refused():
    with pytest.raises(ValueError):
        _sale(closed_at="2026-09-23T10:00:00")


# ─── HTTP edge ────────────────────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    device = SimpleNamespace(id=uuid.uuid4(), branch_id=uuid.uuid4())

    async def fake_db():
        yield AsyncMock()

    pos_app.dependency_overrides[get_current_device] = lambda: device
    pos_app.dependency_overrides[get_db] = fake_db
    yield AsyncClient(transport=ASGITransport(app=pos_app), base_url="http://pos")
    pos_app.dependency_overrides.clear()


def _headers(build: str | None) -> dict:
    headers = {"Content-Type": "application/json", "X-Device-Token": "t"}
    if build is not None:
        headers["X-App-Build"] = build
    return headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "build", [None, "1056", str(COUNTER_LOCAL_FIRST_MIN_BUILD - 1)]
)
async def test_an_old_build_gets_426_before_its_body_is_read(
    client, monkeypatch, build
):
    ingest = AsyncMock()
    monkeypatch.setattr(counter_ingest_service, "ingest", ingest)
    resp = await client.post(
        "/api/v1/pos/counter/sales", headers=_headers(build), content="{}"
    )
    assert resp.status_code == 426
    assert resp.json()["code"] == "upgrade_required"
    ingest.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "status"), [(201, "ingested"), (200, "replayed"), (202, "quarantined")]
)
async def test_the_ingest_status_code_passes_through(client, monkeypatch, code, status):
    sale = _sale()
    result = counter_ingest_service.IngestResult(
        code, CounterSaleResponse(status=status, order_id=sale.id)
    )
    monkeypatch.setattr(
        counter_ingest_service, "ingest", AsyncMock(return_value=result)
    )
    resp = await client.post(
        "/api/v1/pos/counter/sales",
        headers=_headers(str(COUNTER_LOCAL_FIRST_MIN_BUILD)),
        content=sale.model_dump_json(),
    )
    assert resp.status_code == code
    assert resp.json()["status"] == status


@pytest.mark.asyncio
async def test_a_conflict_is_409_with_a_stable_code(client, monkeypatch):
    monkeypatch.setattr(
        counter_ingest_service,
        "ingest",
        AsyncMock(side_effect=counter_ingest_service.SaleConflict("different")),
    )
    resp = await client.post(
        "/api/v1/pos/counter/sales",
        headers=_headers(str(COUNTER_LOCAL_FIRST_MIN_BUILD)),
        content=_sale().model_dump_json(),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "counter_sale_conflict"


@pytest.mark.asyncio
async def test_a_malformed_sale_from_a_new_build_is_422(client):
    resp = await client.post(
        "/api/v1/pos/counter/sales",
        headers=_headers(str(COUNTER_LOCAL_FIRST_MIN_BUILD)),
        content="{}",
    )
    assert resp.status_code == 422


# ─── Till guard ───────────────────────────────────────────────────────────────


def test_till_requests_without_device_pending_sales_are_still_valid():
    close = TillCloseRequest.model_validate({"closing_amount": "10.00"})
    assert close.device_pending_sales is None
    opened = TillOpenRequest.model_validate({"branch_id": str(uuid.uuid4())})
    assert opened.device_pending_sales is None


def _user(*perms: str, admin: bool = False):
    return SimpleNamespace(is_admin=admin, can=lambda p: p in perms)


def test_closing_over_unsynced_sales_needs_a_manager():
    with pytest.raises(ConflictError):
        till_service.ensure_can_close_over_pending(_user("pos.register.access"), 2)
    till_service.ensure_can_close_over_pending(_user("pos.till.manage"), 2)
    till_service.ensure_can_close_over_pending(_user(admin=True), 2)
    till_service.ensure_can_close_over_pending(_user(), 0)


@pytest.mark.asyncio
async def test_a_handover_refuses_over_unsynced_sales(monkeypatch):
    outgoing = SimpleNamespace(user_id=uuid.uuid4())
    monkeypatch.setattr(
        till_service, "open_till_on_device", AsyncMock(return_value=outgoing)
    )
    close = AsyncMock()
    monkeypatch.setattr(till_service, "close_till", close)
    incoming = SimpleNamespace(
        id=uuid.uuid4(),
        is_admin=False,
        can=lambda p: False,
        display_name="B",
        email="b@x",
    )
    with pytest.raises(ConflictError):
        await till_service.handover_on_device(
            AsyncMock(),
            device_id=uuid.uuid4(),
            user=incoming,
            counted=Decimal("0"),
            device_pending_sales=1,
        )
    close.assert_not_awaited()
