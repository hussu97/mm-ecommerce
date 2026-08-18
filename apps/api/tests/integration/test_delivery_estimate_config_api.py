"""
The three delivery-estimate numbers, from the outside.

Every one of them was a deploy before this: noon Send's minutes were a value in
a migration, a batch group's minutes-to-door likewise, and a third party's days
were the literal `1` in the resolver. They are commercial figures — an SLA is
renegotiated, a route re-timed — so the property worth pinning here is not the
arithmetic (that is `test_delivery_estimate.py`) but that the numbers can be
written at all, and that the one combination which makes the resolver fall back
to a figure nobody chose is refused.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.deps import get_current_active_user
from app.models.courier import Courier
from app.models.delivery_batch import DeliveryBatchGroup
from app.models.user import User


def _courier(code: str, *, kind: str, minutes: int | None, days: int = 1) -> Courier:
    return Courier(
        code=code,
        name=code.replace("_", " ").title(),
        supports_batching=code == "lalamove",
        unbatched_promise_kind=kind,
        unbatched_promise_minutes=minutes,
        unbatched_promise_days=days,
        is_active=True,
    )


@pytest.fixture
def admin_client(client):
    """`delivery.manage`; the permission machinery itself is tested elsewhere."""
    from app.main import app

    app.dependency_overrides[get_current_active_user] = lambda: User(
        email="admin@example.com", is_admin=True, is_active=True
    )
    yield client
    app.dependency_overrides.pop(get_current_active_user, None)


@pytest.fixture
def quiet_audit(monkeypatch):
    """Every write here is audited; the audit row is not what these test."""
    from app.services import audit_service

    monkeypatch.setattr(audit_service, "log_action", AsyncMock(return_value=None))


def _rows(mock_db, rows, *, one=None):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    result.all.return_value = []
    result.scalar_one_or_none = MagicMock(return_value=one)
    mock_db.execute = AsyncMock(return_value=result)


class TestCourierPromises:
    async def test_it_lists_what_each_courier_promises(self, admin_client, mock_db):
        _rows(
            mock_db,
            [
                _courier("noon_send", kind="minutes", minutes=90),
                _courier("third_party", kind="next_day", minutes=None, days=2),
            ],
        )

        response = await admin_client.get("/api/v1/delivery-zones/couriers")

        assert response.status_code == 200
        by_code = {c["code"]: c for c in response.json()}
        assert by_code["noon_send"]["unbatched_promise_minutes"] == 90
        assert by_code["third_party"]["unbatched_promise_days"] == 2
        # Read-only, and shown anyway: it is the reason noon Send has no
        # schedule on the batching screen.
        assert by_code["noon_send"]["supports_batching"] is False

    async def test_noon_send_can_be_moved_from_an_hour_to_ninety_minutes(
        self, admin_client, mock_db, quiet_audit
    ):
        """The change this whole screen exists for, made without a deploy."""
        courier = _courier("noon_send", kind="minutes", minutes=60)
        _rows(mock_db, [], one=courier)

        response = await admin_client.put(
            "/api/v1/delivery-zones/couriers/noon_send",
            json={"unbatched_promise_minutes": 90},
        )

        assert response.status_code == 200
        assert response.json()["unbatched_promise_minutes"] == 90
        assert courier.unbatched_promise_minutes == 90

    async def test_a_third_party_can_be_given_more_than_one_day(
        self, admin_client, mock_db, quiet_audit
    ):
        courier = _courier("third_party", kind="next_day", minutes=None)
        _rows(mock_db, [], one=courier)

        response = await admin_client.put(
            "/api/v1/delivery-zones/couriers/third_party",
            json={"unbatched_promise_days": 3},
        )

        assert response.status_code == 200
        assert response.json()["unbatched_promise_days"] == 3

    async def test_promising_minutes_with_no_minutes_set_is_refused(
        self, admin_client, mock_db, quiet_audit
    ):
        """
        The one combination that makes the resolver fall back to its own
        literal — a number nobody chose, quoted to a customer as though
        somebody had. Refused with a sentence, not a 500.
        """
        _rows(mock_db, [], one=_courier("third_party", kind="next_day", minutes=None))

        response = await admin_client.put(
            "/api/v1/delivery-zones/couriers/third_party",
            json={"unbatched_promise_kind": "minutes"},
        )

        assert response.status_code == 400
        assert "minutes" in response.json()["detail"]

    async def test_a_nonsense_promise_kind_is_refused(
        self, admin_client, mock_db, quiet_audit
    ):
        _rows(mock_db, [], one=_courier("noon_send", kind="minutes", minutes=60))

        response = await admin_client.put(
            "/api/v1/delivery-zones/couriers/noon_send",
            json={"unbatched_promise_kind": "whenever"},
        )

        assert response.status_code == 400

    @pytest.mark.parametrize(
        "payload", [{"unbatched_promise_minutes": 0}, {"unbatched_promise_days": 0}]
    )
    async def test_a_promise_of_nothing_is_refused_at_the_schema(
        self, admin_client, mock_db, quiet_audit, payload
    ):
        """Zero minutes is "already there" and zero days is "yesterday"."""
        _rows(mock_db, [], one=_courier("noon_send", kind="minutes", minutes=60))

        response = await admin_client.put(
            "/api/v1/delivery-zones/couriers/noon_send", json=payload
        )

        assert response.status_code == 422

    async def test_an_unknown_courier_is_a_404(
        self, admin_client, mock_db, quiet_audit
    ):
        _rows(mock_db, [], one=None)

        response = await admin_client.put(
            "/api/v1/delivery-zones/couriers/pigeon",
            json={"unbatched_promise_days": 2},
        )

        assert response.status_code == 404


class TestBatchGroupMinutes:
    async def test_a_groups_time_to_the_door_can_be_changed(
        self, admin_client, mock_db, quiet_audit
    ):
        """
        The northern run crosses three emirates on the same rate card as the
        Dubai one, which is why this lives on the group rather than the courier.
        """
        group = DeliveryBatchGroup(
            id=uuid.uuid4(),
            name="Northern Emirates",
            courier_code="lalamove",
            delivery_minutes_after_dispatch=120,
            is_active=True,
        )
        mock_db.get = AsyncMock(return_value=group)
        _rows(mock_db, [])

        response = await admin_client.put(
            f"/api/v1/delivery-zones/batch-groups/{group.id}",
            json={"delivery_minutes_after_dispatch": 150},
        )

        assert response.status_code == 200
        assert response.json()["delivery_minutes_after_dispatch"] == 150
        assert group.delivery_minutes_after_dispatch == 150

    async def test_the_courier_a_group_books_cannot_be_changed_here(
        self, admin_client, mock_db, quiet_audit
    ):
        """
        Not a validation rule — the field simply is not on the write model.
        Which carrier a group books is what `supports_batching` guards and what
        its zones were assigned against; moving it re-partitions the map.
        """
        group = DeliveryBatchGroup(
            id=uuid.uuid4(),
            name="Dubai",
            courier_code="lalamove",
            delivery_minutes_after_dispatch=90,
            is_active=True,
        )
        mock_db.get = AsyncMock(return_value=group)
        _rows(mock_db, [])

        response = await admin_client.put(
            f"/api/v1/delivery-zones/batch-groups/{group.id}",
            json={"courier_code": "noon_send", "delivery_minutes_after_dispatch": 95},
        )

        assert response.status_code == 200
        assert group.courier_code == "lalamove"
        assert group.delivery_minutes_after_dispatch == 95
