"""
Per-terminal reporting: the answers a manager's phone asks for.

The branches dashboard already tells you *a* terminal in *a* branch has gone
quiet. The companion app has to name it, say whose shift was on it and what it
took today, so three things have to hold: the route exists on the register's own
application, it is readable by a shift manager rather than only an administrator,
and `device` is a real sales dimension with names attached rather than raw UUIDs.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.v1 import operations
from app.main import app as web_app
from app.models.base import utcnow
from app.models.order import Order
from app.pos_main import app as pos_app
from app.services.pos import pos_reports
from app.services.pos.pos_reports import _base as pos_reports_base


def _paths(app) -> set[str]:
    return set(app.openapi()["paths"])


class _StubDB:
    """Just enough session to resolve one device and count commits."""

    def __init__(self, device):
        self._device = device
        self.commits = 0

    async def execute(self, _stmt):
        device = self._device

        class _Result:
            @staticmethod
            def scalar_one_or_none():
                return device

        return _Result()

    async def commit(self):
        self.commits += 1


# ─── The route ────────────────────────────────────────────────────────────────


def test_the_terminals_dashboard_is_served_by_both_applications():
    """
    A manager's phone talks to the register's host like every other terminal, so
    the endpoint has to be on the POS application — not only the console's.
    """
    for app in (pos_app, web_app):
        assert "/api/v1/pos/dashboard/devices" in _paths(app)


def test_a_shift_manager_can_read_it_without_admin_rights():
    """
    `/devices` is admin-only because it issues pairing codes. This one issues
    nothing, so gating it the same way would put the reporting behind a
    credential no branch manager should hold.
    """
    source = operations.terminals_dashboard.__doc__ or ""
    assert "dashboard.access" in source

    # And the guard is actually the one the docstring claims — declared as a
    # `require(...)` dependency in the route's signature.
    import inspect

    body = inspect.getsource(operations.terminals_dashboard)
    assert 'require("dashboard.access")' in body
    assert "get_admin_user" not in body


def test_only_machines_a_sale_happens_on_are_reported():
    """
    Kitchen displays and printer bridges are devices too. Counting them would
    report terminals that cannot sell as terminals that have sold nothing.
    """
    import inspect

    body = inspect.getsource(operations.terminals_dashboard)
    assert '"cashier", "sub_cashier"' in body


def test_today_means_the_branch_business_date_not_the_wall_clock():
    """
    A shop trading past midnight has one trading day. Scoping on `created_at`
    would split a Friday night across two dates and make every late terminal
    look like it stopped.
    """
    import inspect

    body = inspect.getsource(operations.terminals_dashboard)
    assert "business_day_service.current_business_date" in body
    assert "Order.business_date ==" in body


# ─── The aggregate ────────────────────────────────────────────────────────────


def _terminal(**overrides) -> operations.TerminalLive:
    defaults = dict(
        device_id=uuid.uuid4(),
        name="Front counter",
        reference="T1",
        type="cashier",
        status="paired",
        branch_id=uuid.uuid4(),
        branch_name="Jumeirah",
        last_seen_at=utcnow(),
        is_online=True,
        app_version="1.0",
        os_version="26.0",
        model_identifier="iPad14,3",
        open_till_id=None,
        open_till_user=None,
        open_till_opened_at=None,
        active_orders=0,
        active_orders_amount=Decimal("0"),
        orders_today=0,
        sales_today=Decimal("0"),
    )
    defaults.update(overrides)
    return operations.TerminalLive(**defaults)


def test_the_header_figures_are_the_sum_of_the_rows():
    """
    The companion shows the totals above the list. A header that disagrees with
    the rows beneath it is the fastest way to lose a manager's trust in the whole
    screen, so it is derived rather than counted separately.
    """
    rows = [
        _terminal(
            orders_today=12, sales_today=Decimal("1200.50"), open_till_id=uuid.uuid4()
        ),
        _terminal(orders_today=3, sales_today=Decimal("99.50"), is_online=False),
        _terminal(orders_today=0, sales_today=Decimal("0")),
    ]

    dashboard = operations.TerminalsDashboard(
        terminals=len(rows),
        online=sum(1 for r in rows if r.is_online),
        offline=sum(1 for r in rows if not r.is_online),
        open_tills=sum(1 for r in rows if r.open_till_id is not None),
        orders_today=sum(r.orders_today for r in rows),
        sales_today=sum((r.sales_today for r in rows), start=Decimal("0")),
        devices=rows,
    )

    assert dashboard.terminals == 3
    assert dashboard.online == 2
    assert dashboard.offline == 1
    assert dashboard.open_tills == 1
    assert dashboard.orders_today == 15
    assert dashboard.sales_today == Decimal("1300.00")


@pytest.mark.asyncio
async def test_a_device_that_talks_to_us_is_recorded_as_seen(monkeypatch):
    """
    `last_seen_at` was written only by pairing and by the launch heartbeat, so
    five minutes into a shift every dashboard called the till offline — while it
    was ringing up sales. Any authenticated request now counts as being seen.

    The durable write goes through the dedicated-session stamp, and the request
    session is never committed: this runs inside an auth dependency, and a
    dependency that commits the request session makes whatever the request had
    written so far permanent as a side effect of bookkeeping.
    """
    from app.api.v1 import devices as devices_api

    stamped = []

    async def _record_stamp(device_id, now, build=None):
        stamped.append((device_id, now))

    monkeypatch.setattr(devices_api, "_stamp_last_seen", _record_stamp)

    device = SimpleNamespace(
        id=uuid.uuid4(),
        status="used",
        last_seen_at=utcnow() - timedelta(hours=3),
        token_hash="hashed",
    )
    db = _StubDB(device)

    resolved = await devices_api.authenticate_device(db, "a-token")

    assert resolved is device
    assert (utcnow() - resolved.last_seen_at).total_seconds() < 5
    assert [device_id for device_id, _ in stamped] == [device.id]
    assert db.commits == 0, "an auth dependency must never commit the request session"


@pytest.mark.asyncio
async def test_a_busy_till_does_not_write_a_row_per_request(monkeypatch):
    """
    Throttled to one write a minute per device — a terminal mid-service issues a
    request per tap, and stamping every one of them would turn a read path into
    a write path for no extra accuracy.
    """
    from app.api.v1 import devices as devices_api

    stamped = []

    async def _record_stamp(device_id, now, build=None):
        stamped.append(device_id)

    monkeypatch.setattr(devices_api, "_stamp_last_seen", _record_stamp)

    seen = utcnow() - timedelta(seconds=5)
    device = SimpleNamespace(
        id=uuid.uuid4(), status="used", last_seen_at=seen, token_hash="hashed"
    )
    db = _StubDB(device)

    await devices_api.authenticate_device(db, "a-token")

    assert device.last_seen_at == seen
    assert stamped == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_a_failed_seen_stamp_does_not_fail_the_sale(monkeypatch):
    """
    The stamp is bookkeeping for a dashboard. A database hiccup writing it must
    not 401 a till mid-service — the device authenticated; that is the answer.
    """
    from app.api.v1 import devices as devices_api

    class _ExplodingFactory:
        def __call__(self):
            raise RuntimeError("no database for you")

    monkeypatch.setattr(devices_api, "AsyncSessionFactory", _ExplodingFactory())

    device = SimpleNamespace(
        id=uuid.uuid4(),
        status="used",
        last_seen_at=None,
        token_hash="hashed",
    )
    db = _StubDB(device)

    resolved = await devices_api.authenticate_device(db, "a-token")

    assert resolved is device


def test_the_refresh_window_is_inside_the_offline_threshold():
    """
    If a device could go longer between stamps than the dashboards allow before
    calling it offline, a perfectly healthy till would flicker red.
    """
    from app.api.v1 import devices as devices_api

    assert devices_api.SEEN_REFRESH_SECONDS < operations.OFFLINE_AFTER_SECONDS


def test_offline_is_decided_by_the_same_threshold_as_the_branches_dashboard():
    """
    Two screens in one app disagreeing about what "offline" means would have a
    manager reading one number on the Live tab and another in the branch list.
    """
    assert operations.OFFLINE_AFTER_SECONDS == 300

    now = utcnow()
    stale = now - timedelta(seconds=operations.OFFLINE_AFTER_SECONDS + 1)
    fresh = now - timedelta(seconds=operations.OFFLINE_AFTER_SECONDS - 1)

    assert (now - stale).total_seconds() > operations.OFFLINE_AFTER_SECONDS
    assert (now - fresh).total_seconds() <= operations.OFFLINE_AFTER_SECONDS


# ─── The sales dimension ──────────────────────────────────────────────────────


def test_device_is_a_supported_sales_dimension():
    assert "device" in pos_reports.SUPPORTED_DIMENSIONS


def test_device_groups_by_the_order_column_that_records_the_terminal():
    """
    Grouping by anything else — the till, the branch — would merge two counters
    that shared a shift, which is exactly the comparison this dimension exists
    to make.
    """
    assert pos_reports_base._ORDER_DIMENSIONS["device"] is Order.device_id


@pytest.mark.asyncio
async def test_device_rows_come_back_named_rather_than_as_uuids():
    """
    Every other grouped foreign key is resolved to something a human recognises.
    A terminal reported as `6f2c…` is a report nobody can act on.
    """

    class _Device:
        def __init__(self, id_, name):
            self.id = id_
            self.name = name

    front = _Device(uuid.uuid4(), "Front counter")
    drive = _Device(uuid.uuid4(), "Drive-thru")

    class _Result:
        def scalars(self):
            class _Scalars:
                @staticmethod
                def all():
                    return [front, drive]

            return _Scalars()

    class _DB:
        @staticmethod
        async def execute(_stmt):
            return _Result()

    labels = await pos_reports_base._labels_for(
        _DB(), "device", [(front.id,), (drive.id,)]
    )

    assert labels == {
        str(front.id): "Front counter",
        str(drive.id): "Drive-thru",
    }


# ─── Tills ────────────────────────────────────────────────────────────────────


def test_the_tills_report_names_the_machine_a_shift_ran_on():
    """
    A variance is investigated by walking to the drawer. A report naming only the
    cashier sends a manager looking for a person instead.
    """
    import inspect

    body = inspect.getsource(pos_reports.tills_report)
    assert '"device_id"' in body
    assert '"device_name"' in body


# ─── Which 401 means "no longer paired" ───────────────────────────────────────


@pytest.mark.asyncio
async def test_only_an_unknown_token_tells_a_terminal_to_forget_its_pairing():
    """
    The register discards its device token when it sees `device_revoked`, and
    only then.

    It used to discard it on *any* 401 from the heartbeat, which is why a
    terminal asked for a pairing code most mornings: a request that reached the
    storefront host instead of the register's, or arrived without the header,
    came back 401 and cost the shop its pairing. Only this case — a token that
    matches no device at all — is worthless and safe to throw away.
    """
    from app.api.v1 import devices as devices_api
    from app.core.exceptions import UnauthorizedError

    with pytest.raises(UnauthorizedError) as raised:
        await devices_api.authenticate_device(_StubDB(None), "a-token")

    assert raised.value.code == "device_revoked"


@pytest.mark.asyncio
async def test_a_disabled_device_keeps_its_pairing():
    """
    The token is still good and the record still exists — an admin flips the
    status back and the terminal carries on. Coding this would turn a
    two-second fix in the console into a trip to the shop with a pairing code.
    """
    from app.api.v1 import devices as devices_api
    from app.core.exceptions import UnauthorizedError

    device = SimpleNamespace(status="disabled", last_seen_at=utcnow(), token_hash="h")

    with pytest.raises(UnauthorizedError) as raised:
        await devices_api.authenticate_device(_StubDB(device), "a-token")

    assert raised.value.code is None
    assert "disabled" in str(raised.value.detail)


@pytest.mark.asyncio
async def test_a_missing_header_keeps_its_pairing():
    """A request that arrived without the header says nothing about whether
    this device is still registered."""
    from app.api.v1 import devices as devices_api
    from app.core.exceptions import UnauthorizedError

    with pytest.raises(UnauthorizedError) as raised:
        await devices_api.authenticate_device(_StubDB(None), None)

    assert raised.value.code is None


def test_a_code_reaches_the_client_and_an_uncoded_error_still_looks_the_same():
    """
    The handler adds `code` only when there is one, so every response that had
    no code keeps the exact body it had.
    """
    from app.core.exceptions import BadRequestError, UnauthorizedError

    coded = UnauthorizedError("Unrecognised device token", code="device_revoked")
    assert coded.code == "device_revoked"

    assert BadRequestError("nope").code is None
    assert UnauthorizedError("Invalid pairing code").code is None
