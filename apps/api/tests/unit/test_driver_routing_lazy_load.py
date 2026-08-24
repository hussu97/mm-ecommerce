"""
`route_now` must never touch an unloaded `delivery.order`.

The regression this pins down: the order was resolved as

    order = delivery.order or (await db.execute(...)).scalars().first()

written to mean "use the relationship if we have it, otherwise fetch it". Under
asyncpg an unloaded relationship does not evaluate falsy — it raises
`MissingGreenlet` — so the fallback was unreachable and the expression meant to
make the read safe was the thing that threw.

It threw *conditionally*, which is what let it survive review and the whole
test suite: if anything earlier in the request had already pulled the order into
the session's identity map, the attribute resolved with no IO and the same code
passed. Slider was the path that exposed it, because `_delivery_for` did not
eager-load the order the way Lalamove's lookup does. It fired on
`rider_assigned` — the first driver on a booking, the one push that prints a
slip at the counter.

The fake below stands in for that: `.order` raises exactly as SQLAlchemy would.
A `route_now` that reads it fails this test.
"""

import uuid

import pytest
from sqlalchemy.exc import MissingGreenlet

from app.services.delivery import driver_routing


class _ExplodingOrder:
    """An `OrderDelivery` whose `.order` behaves like an unloaded relationship."""

    def __init__(self, order_id):
        self.order_id = order_id
        self.driver_latitude = 25.36
        self.driver_longitude = 55.44

    @property
    def is_driver_on_the_way_here(self) -> bool:
        return True

    @property
    def order(self):
        raise MissingGreenlet(
            "greenlet_spawn has not been called; can't call await_only() here."
        )


@pytest.mark.asyncio
async def test_route_now_does_not_read_an_unloaded_order(monkeypatch):
    monkeypatch.setattr(driver_routing.mapbox_provider, "is_configured", lambda: True)

    class _Result:
        def scalars(self):
            return self

        def first(self):
            return None

    class _Db:
        def __init__(self):
            self.queries = 0

        async def get(self, _model, _pk):
            self.queries += 1
            return None

        async def execute(self, *_a, **_kw):  # pragma: no cover — must not run
            raise AssertionError("route_now should resolve the order by id")

    db = _Db()
    delivery = _ExplodingOrder(uuid.uuid4())

    # No MissingGreenlet. The order is absent, so this is a quiet False rather
    # than a 500 on a webhook that has to answer 200.
    assert await driver_routing.route_now(db, delivery) is False
    # And it went and looked, rather than giving up on the attribute read.
    assert db.queries == 1
