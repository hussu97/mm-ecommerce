"""
The batching screen's one query, which spent its first day returning a 500.

`delivery_batches` moved from hanging off a polygon to hanging off a group when
groups were introduced — a run belongs to the set of zones that ride together,
not to whichever zone's slot happened to open it. Every other caller followed;
these two did not, and nothing here asked them to. The endpoint read
`batch.polygon_id` off a model that no longer has one, and `BatchResponse`
declared `polygon_id` while its own constructor passed `group_id`, so even the
attribute error was hiding a second one behind it.

Both are attribute-level mistakes on a route with no test, which is the only
reason they reached production. This calls the route function directly — no
client, no auth — because the query is the thing that broke.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.api.v1.delivery_zones import BatchResponse, list_batches
from app.models.delivery_batch import BatchStatusEnum, DeliveryBatch


def _batch(group_id: uuid.UUID) -> DeliveryBatch:
    return DeliveryBatch(
        id=uuid.uuid4(),
        group_id=group_id,
        window_label="Batch 6",
        dispatch_at=datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc),
        status=BatchStatusEnum.PENDING.value,
        stop_count=1,
        attempt_count=0,
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    """Answers the route's three queries in the order it asks them."""

    def __init__(self, batch: DeliveryBatch, group_name: str, on_run: list[tuple]):
        self._answers = [
            [batch],
            [(batch.group_id, group_name)],
            on_run,
        ]

    async def execute(self, _stmt):
        return _Result(self._answers.pop(0))


@pytest.mark.asyncio
async def test_the_batching_screen_loads():
    """The 500 itself: `polygon_id` read off a batch that is keyed on a group."""
    group_id = uuid.uuid4()
    batch = _batch(group_id)
    db = _Session(batch, "Dubai", [(batch.id, "Dubai Near", "MM-20260819-001")])

    rows = await list_batches(db=db, _admin=None)

    assert len(rows) == 1
    assert rows[0].group_id == str(group_id)
    assert rows[0].order_numbers == ["MM-20260819-001"]


@pytest.mark.asyncio
async def test_a_run_with_nothing_on_it_yet_names_its_group():
    """
    The fallback. A run is opened by the first order in its window, and between
    that and the next sweep it has no delivery rows to name — so the honest
    answer is the group whose schedule opened it, which is also the only zone
    grouping a person on the batching screen can act on.
    """
    group_id = uuid.uuid4()
    batch = _batch(group_id)
    db = _Session(batch, "Northern Emirates", [])

    rows = await list_batches(db=db, _admin=None)

    assert rows[0].zone_name == "Northern Emirates"


def test_the_response_carries_the_field_it_declares():
    """
    `BatchResponse.of` passed `group_id=` into a model declaring `polygon_id`.
    Pydantic drops the unknown keyword and then fails on the missing required
    one, so this never depended on the database at all.
    """
    group_id = uuid.uuid4()

    response = BatchResponse.of(_batch(group_id), "Dubai", [])

    assert response.group_id == str(group_id)
    assert not hasattr(response, "polygon_id")
