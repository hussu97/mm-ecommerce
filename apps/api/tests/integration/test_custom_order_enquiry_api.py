"""
The public custom-order enquiry endpoint stores a lead and emails the shop —
and, crucially, creates no order and no CustomOrder (it is a message, not a
booking). Drives the real route through the ASGI app against a real Postgres.

SKIPs unless `TEST_DATABASE_URL` (or `DATABASE_URL`) is set.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]

MARKER = "pytest-enq-"


@pytest.fixture
async def maker():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(maker):
    from httpx import ASGITransport, AsyncClient

    from app.core.deps import get_db
    from app.main import app

    async def override_get_db():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
async def cleanup(maker):
    yield
    from app.models.custom_order_enquiry import CustomOrderEnquiry

    async with maker() as db:
        await db.execute(
            delete(CustomOrderEnquiry).where(
                CustomOrderEnquiry.customer_name.like(f"{MARKER}%")
            )
        )
        await db.commit()


async def test_enquiry_stores_lead_and_emails_without_creating_an_order(
    client, maker, monkeypatch
):
    import uuid

    from app.core import object_storage
    from app.models.custom_order_enquiry import CustomOrderEnquiry
    from app.models.order import Order
    from app.services import email_service

    sent: list = []

    async def _spy(*, enquiry):
        sent.append(enquiry)

    monkeypatch.setattr(email_service, "send_custom_order_enquiry", _spy)
    # The photos are pending uploads in the private bucket; fake the bucket.
    pending = [f"enquiries/pending/{uuid.uuid4()}.jpg" for _ in range(2)]
    copies: list[tuple[str, str]] = []
    monkeypatch.setattr(object_storage, "object_exists", lambda **kw: True)
    monkeypatch.setattr(
        object_storage,
        "copy_object",
        lambda *, bucket, src_key, dst_key: copies.append((src_key, dst_key)),
    )
    monkeypatch.setattr(object_storage, "delete_object", lambda **kw: None)

    async with maker() as db:
        orders_before = int(
            (await db.execute(select(func.count()).select_from(Order))).scalar() or 0
        )

    due = (date.today() + timedelta(days=20)).isoformat()
    res = await client.post(
        "/api/v1/custom-orders/enquiry",
        json={
            "customer_name": f"{MARKER}sara",
            "customer_phone": "0501234567",
            "description": "Three-tier pistachio, sage & gold, for a nikkah",
            "approx_kg": 4.5,
            "reference_image_urls": pending,
            "delivery_by": due,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["id"] and body["created_at"]

    # The lead landed, with the phone normalised to E.164.
    async with maker() as db:
        row = (
            await db.execute(
                select(CustomOrderEnquiry).where(
                    CustomOrderEnquiry.customer_name == f"{MARKER}sara"
                )
            )
        ).scalar_one()
        assert row.customer_phone == "+971501234567"
        # Stored as keys under the enquiry, moved out of `pending/`.
        assert row.reference_image_urls == [
            f"enquiries/{row.id}/{k.rsplit('/', 1)[1]}" for k in pending
        ]
        assert [src for src, _ in copies] == pending
        assert str(row.delivery_by) == due

        # No order was created — this is a lead, not an order.
        orders_after = int(
            (await db.execute(select(func.count()).select_from(Order))).scalar() or 0
        )
    assert orders_after == orders_before

    # The shop was emailed exactly once, with the row we stored.
    assert len(sent) == 1
    assert sent[0].customer_name == f"{MARKER}sara"


async def test_enquiry_rejects_more_than_four_images(client, monkeypatch):
    from app.services import email_service

    async def _spy(*, enquiry):
        pass

    monkeypatch.setattr(email_service, "send_custom_order_enquiry", _spy)

    res = await client.post(
        "/api/v1/custom-orders/enquiry",
        json={
            "customer_name": f"{MARKER}toomany",
            "customer_phone": "+971501234567",
            "description": "Lots of inspiration",
            "reference_image_urls": [f"https://example.com/{i}.jpg" for i in range(5)],
        },
    )
    # Caught by the schema's max_length on the list → 422.
    assert res.status_code == 422, res.text


async def test_enquiry_refuses_a_photo_url_that_is_not_our_upload(client, monkeypatch):
    from app.services import email_service

    async def _spy(*, enquiry):
        raise AssertionError("no email for a refused enquiry")

    monkeypatch.setattr(email_service, "send_custom_order_enquiry", _spy)

    res = await client.post(
        "/api/v1/custom-orders/enquiry",
        json={
            "customer_name": f"{MARKER}phish",
            "customer_phone": "+971501234567",
            "description": "See my photo",
            "reference_image_urls": ["https://evil.example/login.jpg"],
        },
    )
    assert res.status_code == 400, res.text
