"""
The admin's log screens must be able to render a row that actually exists.

Both `audit_logs` and `email_logs` read rows straight off the ORM into a
response model. That is the cheap, obvious way to write those endpoints, and it
has one trap: a `UUID` column typed as `str` in the model. Pydantic v2 does not
coerce between them — it raises — so *every* response becomes a 500 and the
screen shows an empty table with no error anywhere a user can see.

It went unnoticed for months. The tables filled up the whole time: 69 audit rows
and 71 email rows, the newest of them from the same day it was reported.

These tests build each response model from a real ORM instance, which is the
only shape that reproduces it. A field-by-field assertion would not: the bug
lives in the gap between the column's type and the model's.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.api.v1.audit_logs import AuditLogItem
from app.api.v1.email_logs import EmailLogItem
from app.models.audit_log import AuditLog
from app.models.email_log import EmailLog

NOW = datetime(2026, 8, 4, 14, 13, tzinfo=timezone.utc)


def test_an_email_log_row_serialises():
    row = EmailLog(
        id=uuid.uuid4(),
        template="order_confirmation",
        recipient="customer@example.com",
        subject="Your order MM-20260804-001",
        order_number="MM-20260804-001",
        status="sent",
        resend_id="re_abc123",
        error=None,
        sent_at=NOW,
    )

    item = EmailLogItem.model_validate(row)
    payload = item.model_dump(mode="json")

    # A string in the JSON, which is what the admin's types expect — the column
    # being a UUID is an implementation detail that must not reach the browser
    # as one, nor stop the response being built in the first place.
    assert isinstance(payload["id"], str)
    assert payload["template"] == "order_confirmation"


def test_an_audit_log_row_serialises():
    row = AuditLog(
        id=uuid.uuid4(),
        action="UPDATE",
        entity_type="branch",
        entity_id=str(uuid.uuid4()),
        entity_label="K001 Melting Moments Cakes",
        admin_id=uuid.uuid4(),
        admin_email="admin@meltingmomentscakes.com",
        changes={"noon_send_outlet_code": ["", "MLTNGM1GBF"]},
        ip_address="10.212.0.2",
        created_at=NOW,
    )

    payload = AuditLogItem.model_validate(row).model_dump(mode="json")

    assert isinstance(payload["id"], str)
    assert isinstance(payload["admin_id"], str)
    assert payload["changes"] == {"noon_send_outlet_code": ["", "MLTNGM1GBF"]}


def test_an_audit_row_can_name_its_entity_however_it_likes():
    """
    `entity_id` is a `varchar`, not a UUID, and production proves it: an order
    logs `MM-20260605-001` while a product logs its id. Typing it as a UUID
    fixed the screen for every row except the ones about orders, and took the
    page down again the moment one was in range — which is exactly what
    happened on the first attempt at this fix.
    """
    for entity_id in ("MM-20260605-001", str(uuid.uuid4()), "K001"):
        AuditLogItem.model_validate(
            AuditLog(
                id=uuid.uuid4(),
                action="STATUS_CHANGE",
                entity_type="order",
                entity_id=entity_id,
                entity_label="MM-20260605-001",
                admin_id=uuid.uuid4(),
                admin_email="admin@meltingmomentscakes.com",
                changes={"status": ["created", "cancelled"]},
                ip_address=None,
                created_at=NOW,
            )
        )


@pytest.mark.parametrize(
    "blank",
    [
        {"entity_label": None},
        {"admin_id": None, "admin_email": None},
        {"changes": None},
        {"ip_address": None},
    ],
)
def test_an_audit_row_with_a_gap_still_renders(blank):
    """
    Every one of these columns is nullable, and a row written by a background
    job has no admin at all. Typing them as required would take the whole page
    down for one blank cell — the same failure mode as the UUID, arriving by a
    different route.
    """
    values = {
        "id": uuid.uuid4(),
        "action": "STATUS_CHANGE",
        "entity_type": "order",
        "entity_id": "MM-20260804-001",
        "entity_label": "MM-20260804-001",
        "admin_id": uuid.uuid4(),
        "admin_email": "admin@meltingmomentscakes.com",
        "changes": {"status": ["packed", "out_for_delivery"]},
        "ip_address": None,
        "created_at": NOW,
    }
    values.update(blank)

    AuditLogItem.model_validate(AuditLog(**values))
