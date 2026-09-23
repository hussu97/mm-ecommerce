"""
Booking a local-first counter sale (`counter_ingest_service.ingest`), end to end.

Real Postgres: the ingest runs the whole counter lifecycle in one transaction —
advisory locks, the shared check number, the till ledger, inventory depletion,
CHECK constraints (`ck_orders_closed_has_closed_at`), partial unique indexes —
none of which a mocked session exercises. Each test builds its own shop
(`_counter_world.build_world`), plays the register against the bundle the server
publishes (`device_sale`) and syncs the result.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.base import utcnow
from app.models.branch import Branch, BranchBusinessDay
from app.models.device import Device
from app.models.inventory import InventoryLevel
from app.models.marketing import Promotion
from app.models.order import Order, OrderStatusEnum
from app.models.pos_counter import CounterSaleQuarantine, PosConfigBundle
from app.models.pos_order import KitchenTicket, OrderPayment
from app.models.product import Product
from app.models.till import DrawerOperation, Till, TillStatusEnum
from app.models.user import User
from app.services import email_service
from app.services.pos import (
    business_day_service,
    counter_bundle_service,
    counter_ingest_service,
    pos_order_service,
)

from ._counter_world import World, build_world, device_sale, purge_inventory

DATABASE_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

pytestmark = [
    pytest.mark.skipif(
        not DATABASE_URL, reason="needs a database — set TEST_DATABASE_URL"
    ),
    pytest.mark.asyncio,
]


@pytest.fixture
async def Session():
    engine = create_async_engine(DATABASE_URL)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def alerts(monkeypatch):
    """Capture the mismatch alert instead of sending it."""
    sent: list[dict] = []

    async def fake(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(email_service, "send_counter_pricing_mismatch", fake)
    return sent


async def _world(Session, **kwargs) -> World:
    async with Session() as db:
        world = await build_world(db, **kwargs)
        await db.commit()
    return world


async def _sale(Session, world: World, **kwargs):
    async with Session() as db:
        sale = await device_sale(db, world, **kwargs)
        await db.commit()
    return sale


async def _ingest(Session, world: World, sale):
    async with Session() as db:
        device = await db.get(Device, world.device_id)
        result = await counter_ingest_service.ingest(db, device=device, sale=sale)
        await db.commit()
    return result


async def _order(Session, order_id) -> Order:
    async with Session() as db:
        return await pos_order_service.get_order(db, order_id)


# ─── The happy path ───────────────────────────────────────────────────────────


async def test_a_synced_sale_is_booked_verified_paid_closed_and_depleted(Session):
    world = await _world(Session, inventory=True)
    try:
        await _happy_path(Session, world)
    finally:
        await purge_inventory(Session, world)


async def _happy_path(Session, world: World) -> None:
    sale = await _sale(Session, world, lines=[(world.brownie_id, 2)])

    result = await _ingest(Session, world, sale)

    assert result.status_code == 201
    body = result.response
    assert body.status == "ingested"
    assert body.pricing_status == "verified"
    assert body.display_number == sale.display_number == "T1-0001"
    assert body.order_number == f"POS-{world.reference}-{world.business_date}-T1-0001"
    assert body.check_number == 1

    order = await _order(Session, sale.id)
    assert order.id == sale.id and order.client_request_id == sale.id
    assert order.pos_status == "closed"
    assert order.status == OrderStatusEnum.DELIVERED
    assert order.total == Decimal("42.00")
    assert order.vat_amount == Decimal("2.00")
    assert order.balance_due == Decimal("0")
    # The device's clock, not the sync's.
    assert order.created_at == sale.opened_at
    assert order.opened_at == sale.opened_at
    assert order.closed_at == sale.closed_at
    assert order.config_bundle_hash == sale.bundle_hash
    assert order.ingested_at is not None and order.ingested_late is False
    assert order.ingest_flags == []

    async with Session() as db:
        tickets = (
            (
                await db.execute(
                    select(KitchenTicket).where(KitchenTicket.order_id == sale.id)
                )
            )
            .scalars()
            .all()
        )
        assert [(t.origin, t.sequence) for t in tickets] == [("device", 1)]
        assert tickets[0].printed_at == sale.kitchen_tickets[0].printed_at
        payment = (
            await db.execute(
                select(OrderPayment).where(OrderPayment.order_id == sale.id)
            )
        ).scalar_one()
        assert payment.recorded_at == sale.tenders[0].taken_at
        assert payment.idempotency_key == sale.tenders[0].idempotency_key
        assert payment.change_given == Decimal("5.00")
        drawer = (
            await db.execute(
                select(DrawerOperation).where(DrawerOperation.order_id == sale.id)
            )
        ).scalar_one()
        assert drawer.amount == Decimal("42.00")
        till = await db.get(Till, world.till_id)
        assert till.estimated_cash == Decimal("142.00")
        # Two brownies at 2 g of sugar each.
        level = (
            await db.execute(
                select(InventoryLevel).where(
                    InventoryLevel.item_id == world.ingredient_id
                )
            )
        ).scalar_one()
        assert Decimal(str(level.quantity)) == Decimal("-4")


# ─── Replay and conflict ──────────────────────────────────────────────────────


async def test_a_retry_replays_and_a_different_payload_conflicts(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    first = await _ingest(Session, world, sale)
    assert first.status_code == 201

    # The same sale, with a print result recorded after the first attempt.
    retried = sale.model_copy(
        update={"receipt_printed_at": sale.closed_at + timedelta(seconds=9)}
    )
    again = await _ingest(Session, world, retried)
    assert again.status_code == 200
    assert again.response.status == "replayed"
    assert again.response.order_number == first.response.order_number

    changed = sale.model_copy(update={"customer_name": "Someone else"})
    with pytest.raises(counter_ingest_service.SaleConflict):
        await _ingest(Session, world, changed)

    async with Session() as db:
        count = (
            (
                await db.execute(
                    select(OrderPayment).where(OrderPayment.order_id == sale.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(count) == 1, "a replay must not take the money twice"


async def test_two_concurrent_syncs_of_one_sale_book_it_once(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    results = await asyncio.gather(
        _ingest(Session, world, sale), _ingest(Session, world, sale)
    )
    assert sorted(r.status_code for r in results) == [200, 201]


# ─── Pricing ──────────────────────────────────────────────────────────────────


async def test_a_price_edited_after_the_sale_still_verifies_against_its_bundle(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    async with Session() as db:
        product = await db.get(Product, world.brownie_id)
        product.base_price = Decimal("99.00")
        await db.commit()

    result = await _ingest(Session, world, sale)
    assert result.response.pricing_status == "verified"
    order = await _order(Session, sale.id)
    assert order.total == Decimal("21.00")
    assert order.items[0].base_price == Decimal("21.00")


async def test_a_mismatch_books_the_receipt_keeps_the_audit_and_alerts(Session, alerts):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    # A drifted engine: the register printed 20.00 for a 21.00 brownie.
    tampered = sale.model_copy(deep=True)
    tampered.totals.total = Decimal("20.00")
    tampered.totals.total_excl_tax = Decimal("19.05")
    tampered.totals.tax_total = Decimal("0.95")
    tampered.totals.subtotal = Decimal("20.00")
    tampered.totals.taxes[0].amount = Decimal("0.95")
    tampered.totals.taxes[0].taxable_amount = Decimal("19.05")
    tampered.lines[0].totals.total_price = Decimal("20.00")
    tampered.tenders[0].amount = Decimal("20.00")

    result = await _ingest(Session, world, tampered)
    assert result.status_code == 201
    assert result.response.pricing_status == "mismatch"
    assert "pricing_mismatch" in result.response.flags

    order = await _order(Session, sale.id)
    assert order.total == Decimal("20.00"), "the books must match the paper"
    assert order.vat_amount == Decimal("0.95")
    assert order.balance_due == Decimal("0")
    assert order.pos_status == "closed"
    assert order.pricing_audit["server"]["total"] == "21.00"
    assert {d["field"] for d in order.pricing_audit["differences"]} >= {"total"}
    assert len(alerts) == 1 and alerts[0]["client_total"] == "20.00"


async def test_an_unknown_bundle_prices_from_current_data_unverified(Session):
    world = await _world(Session)
    sale = await _sale(
        Session, world, lines=[(world.brownie_id, 1)], bundle_hash="0" * 64
    )
    result = await _ingest(Session, world, sale)
    assert result.response.pricing_status == "unverified"
    assert "unknown_bundle" in result.response.flags
    order = await _order(Session, sale.id)
    assert order.total == Decimal("21.00")


async def test_a_category_promotion_prices_and_verifies(Session):
    world = await _world(Session)
    async with Session() as db:
        db.add(
            Promotion(
                name="Cookies 15%",
                type="basic",
                trigger="spend",
                trigger_value=Decimal("0"),
                reward="percentage_off_order",
                reward_value=Decimal("15"),
                category_ids=[world.category_id],
                sources=["cashier"],
                auto_branch_ids=[world.branch_id],
                auto_apply=True,
                priority=100,
            )
        )
        await db.commit()
    sale = await _sale(
        Session, world, lines=[(world.cookie_id, 2), (world.brownie_id, 1)]
    )
    assert sale.totals.promotion_id is not None
    assert sale.totals.discount_total == Decimal("3.75")
    result = await _ingest(Session, world, sale)
    assert result.response.pricing_status == "verified"
    order = await _order(Session, sale.id)
    # 2 × 12.50 cookies less 15% (3.75), plus a 21.00 brownie at full price.
    assert order.total == Decimal("42.25")
    discounted = [d for d in order.order_discounts if d.source == "promotion"]
    assert len(discounted) == 1 and discounted[0].amount == Decimal("3.75")


# ─── Late tills and days ──────────────────────────────────────────────────────


async def test_a_sale_synced_after_its_till_closed_restates_the_till(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    async with Session() as db:
        till = await db.get(Till, world.till_id)
        cashier = await db.get(User, world.cashier_id)
        await pos_order_service.till_service.close_till(
            db, till=till, closed_by=cashier, closing_amount=Decimal("121.00")
        )
        await db.commit()
        assert till.variance == Decimal("21.00")

    result = await _ingest(Session, world, sale)
    assert result.status_code == 201
    assert result.response.ingested_late is True
    assert "till_closed" in result.response.flags
    async with Session() as db:
        till = await db.get(Till, world.till_id)
        assert till.status == TillStatusEnum.CLOSED.value
        assert till.estimated_cash == Decimal("121.00")
        assert till.variance == Decimal("0.00"), "the late cash sale explains it"
        assert till.totals_restated_at is not None
        assert till.totals["orders_count"] == 1


async def test_a_sale_synced_after_its_day_closed_restates_the_day(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    async with Session() as db:
        till = await db.get(Till, world.till_id)
        cashier = await db.get(User, world.cashier_id)
        branch = await db.get(Branch, world.branch_id)
        await pos_order_service.till_service.close_till(
            db, till=till, closed_by=cashier, closing_amount=Decimal("100.00")
        )
        await business_day_service.get_or_open(db, branch)
        await business_day_service.close_current(db, branch)
        await db.commit()

    result = await _ingest(Session, world, sale)
    assert {"till_closed", "day_closed"} <= set(result.response.flags)
    async with Session() as db:
        day = (
            await db.execute(
                select(BranchBusinessDay).where(
                    BranchBusinessDay.branch_id == world.branch_id,
                    BranchBusinessDay.business_date == world.business_date,
                )
            )
        ).scalar_one()
        assert day.total_orders == 1
        assert Decimal(str(day.total_sales)) == Decimal("21.00")


# ─── Flags, not refusals ──────────────────────────────────────────────────────


async def test_an_inactive_product_is_booked_and_flagged(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    async with Session() as db:
        (await db.get(Product, world.brownie_id)).is_active = False
        await db.commit()
    result = await _ingest(Session, world, sale)
    assert result.status_code == 201
    assert "inactive_product" in result.response.flags
    assert result.response.pricing_status == "verified"


async def test_a_bad_or_missing_attestation_is_flagged_not_refused(Session):
    world = await _world(Session)
    missing = await _sale(
        Session, world, lines=[(world.brownie_id, 1)], attestation=None
    )
    forged = await _sale(
        Session, world, lines=[(world.brownie_id, 1)], seq=2, attestation="x.y.z"
    )
    good = await _sale(Session, world, lines=[(world.brownie_id, 1)], seq=3)
    for sale in (missing, forged):
        result = await _ingest(Session, world, sale)
        assert result.status_code == 201
        assert "attestation_invalid" in result.response.flags
    result = await _ingest(Session, world, good)
    assert "attestation_invalid" not in result.response.flags


async def test_an_expired_attestation_within_the_window_is_accepted(Session):
    """The 12 h PIN token may have lapsed by the time the outbox drains."""
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    async with Session() as db:
        problem = await counter_ingest_service.attestation_problem(
            db,
            token=sale.staff_attestation,
            cashier_id=world.cashier_id,
            branch_id=world.branch_id,
            opened_at=utcnow() + timedelta(hours=12, minutes=30),
        )
        assert problem is None
        late = await counter_ingest_service.attestation_problem(
            db,
            token=sale.staff_attestation,
            cashier_id=world.cashier_id,
            branch_id=world.branch_id,
            opened_at=utcnow() + timedelta(hours=14),
        )
        assert late == "outside_window"


async def test_a_business_date_disagreeing_with_the_close_is_flagged(Session):
    world = await _world(Session)
    sale = await _sale(
        Session,
        world,
        lines=[(world.brownie_id, 1)],
        closed_at=datetime.now(timezone.utc) - timedelta(days=2),
    )
    result = await _ingest(Session, world, sale)
    assert result.status_code == 201
    assert "business_date_disagrees" in result.response.flags


# ─── Quarantine ───────────────────────────────────────────────────────────────


async def test_a_structurally_wrong_sale_is_quarantined_not_refused(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    wrong = sale.model_copy(update={"ticket_prefix": "T9", "display_number": "T9-0001"})

    result = await _ingest(Session, world, wrong)
    assert result.status_code == 202
    assert result.response.status == "quarantined"
    assert result.response.error.startswith("wrong_ticket_prefix")
    again = await _ingest(Session, world, wrong)
    assert again.status_code == 202, "a retry of a quarantined sale is idempotent"

    async with Session() as db:
        row = await db.get(CounterSaleQuarantine, sale.id)
        assert row is not None and row.resolved_at is None
        assert await db.get(Order, sale.id) is None


async def test_tenders_that_do_not_cover_the_receipt_are_quarantined(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)])
    short = sale.model_copy(deep=True)
    short.tenders[0].amount = Decimal("10.00")
    result = await _ingest(Session, world, short)
    assert result.status_code == 202
    assert result.response.error.startswith("tenders_do_not_match_total")


# ─── Numbering ────────────────────────────────────────────────────────────────


async def test_the_order_number_fits_even_for_a_long_branch_reference(Session):
    world = await _world(Session, reference=f"VERYLONGREF{uuid.uuid4().hex[:20]}")
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)], seq=1234)
    result = await _ingest(Session, world, sale)
    number = result.response.order_number
    assert len(number) <= 40
    async with Session() as db:
        branch = await db.get(Branch, world.branch_id)
        expected = pos_order_service.display_reference(branch)
    assert len(expected) == 10
    assert number == f"POS-{expected}-{world.business_date}-T1-1234"


async def test_check_numbers_are_shared_with_server_checks_and_stay_unique(Session):
    """An old terminal's server-numbered check and a new terminal's synced sale
    draw from the same per-branch counter; both close normally."""
    world = await _world(Session)
    async with Session() as db:
        branch = await db.get(Branch, world.branch_id)
        cashier = await db.get(User, world.cashier_id)
        till = await db.get(Till, world.till_id)
        server = await pos_order_service.open_order(
            db, branch=branch, user=cashier, till=till
        )
        await pos_order_service.add_item(
            db, order=server, user=cashier, product_id=world.brownie_id
        )
        server = await pos_order_service.get_order(db, server.id)
        await pos_order_service.record_payment(
            db,
            order=server,
            user=cashier,
            payment_method_id=world.card_id,
            amount=Decimal("21.00"),
            till=till,
        )
        await pos_order_service.close_order(db, order=server, user=cashier)
        await db.commit()
        server_id, server_check = server.id, server.check_number

    sales = [
        await _sale(Session, world, lines=[(world.brownie_id, 1)], seq=n)
        for n in (1, 2)
    ]
    results = await asyncio.gather(*(_ingest(Session, world, s) for s in sales))
    checks = {r.response.check_number for r in results} | {server_check}
    assert len(checks) == 3
    server_order = await _order(Session, server_id)
    assert server_order.display_number is None
    assert server_order.order_number.endswith(f"-{server_check:04d}")
    assert server_order.pos_status == "closed"


async def test_the_bundle_resumes_numbering_from_the_last_ingested_ticket(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)], seq=7)
    await _ingest(Session, world, sale)
    async with Session() as db:
        device = await db.get(Device, world.device_id)
        served = await counter_bundle_service.serve(
            db, device=device, build_number="999999"
        )
        assert served.envelope.last_ingested_ticket_seq == 7
        assert await db.get(PosConfigBundle, served.hash) is not None


# ─── Void ─────────────────────────────────────────────────────────────────────


async def test_a_voided_local_check_is_recorded_as_a_void(Session):
    world = await _world(Session)
    sale = await _sale(Session, world, lines=[(world.brownie_id, 1)], state="void")
    result = await _ingest(Session, world, sale)
    assert result.status_code == 201
    order = await _order(Session, sale.id)
    assert order.pos_status == "void"
    assert order.status == OrderStatusEnum.CANCELLED
    assert order.total == Decimal("0")


# ─── Promote and shadow ───────────────────────────────────────────────────────


async def test_promote_moves_an_untendered_local_check_to_the_server(Session):
    from app.schemas.pos_counter import CounterPromoteRequest

    world = await _world(Session)
    check_id, line_a, line_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    opened = datetime.now(timezone.utc) - timedelta(minutes=4)
    request = CounterPromoteRequest.model_validate(
        {
            "id": str(check_id),
            "branch_id": str(world.branch_id),
            "till_id": str(world.till_id),
            "device_id": str(world.device_id),
            "opened_at": opened.isoformat(),
            "lines": [
                {"id": str(line_a), "product_id": str(world.brownie_id), "quantity": 1},
                {"id": str(line_b), "product_id": str(world.cookie_id), "quantity": 2},
            ],
            "kitchen_tickets": [
                {
                    "sequence": 1,
                    "line_ids": [str(line_a)],
                    "sent_at": (opened + timedelta(minutes=1)).isoformat(),
                    "printed_at": (opened + timedelta(minutes=1)).isoformat(),
                }
            ],
        }
    )
    for _ in range(2):  # idempotent
        async with Session() as db:
            branch = await db.get(Branch, world.branch_id)
            cashier = await db.get(User, world.cashier_id)
            till = await db.get(Till, world.till_id)
            order = await counter_ingest_service.promote(
                db,
                user=cashier,
                branch=branch,
                till=till,
                device_id=world.device_id,
                request=request,
            )
            await db.commit()
    order = await _order(Session, check_id)
    assert order.pos_status == "active" and order.display_number is None
    assert order.created_at == opened
    assert len(order.items) == 2
    assert order.total == Decimal("46.00")
    async with Session() as db:
        tickets = (
            (
                await db.execute(
                    select(KitchenTicket).where(KitchenTicket.order_id == check_id)
                )
            )
            .scalars()
            .all()
        )
        assert [(t.origin, t.sequence) for t in tickets] == [("device", 1)]


async def test_promote_keeps_the_ticket_number_already_on_the_docket(Session):
    """A check fired to the kitchen as T1-000n and then moved to the server
    keeps that number, so the receipt and the docket name the same check; a
    number this device does not own falls back to server numbering."""
    from app.schemas.pos_counter import CounterPromoteRequest
    from app.services.pos import counter_bundle_service

    world = await _world(Session)
    async with Session() as db:
        device = await db.get(Device, world.device_id)
        prefix = await counter_bundle_service.ensure_ticket_prefix(db, device)
        await db.commit()

    async def promote(prefix_: str, seq: int) -> Order:
        line = uuid.uuid4()
        request = CounterPromoteRequest.model_validate(
            {
                "id": str(uuid.uuid4()),
                "branch_id": str(world.branch_id),
                "till_id": str(world.till_id),
                "device_id": str(world.device_id),
                "lines": [
                    {"id": str(line), "product_id": str(world.cookie_id), "quantity": 1}
                ],
                "ticket_prefix": prefix_,
                "ticket_seq": seq,
                "display_number": f"{prefix_}-{seq:04d}",
                "business_date": world.business_date,
            }
        )
        async with Session() as db:
            order = await counter_ingest_service.promote(
                db,
                user=await db.get(User, world.cashier_id),
                branch=await db.get(Branch, world.branch_id),
                till=await db.get(Till, world.till_id),
                device_id=world.device_id,
                request=request,
            )
            await db.commit()
        return await _order(Session, order.id)

    kept = await promote(prefix, 7)
    assert kept.display_number == f"{prefix}-0007"
    assert kept.order_number.endswith(f"-{prefix}-0007")
    foreign = await promote("T9", 8)
    assert foreign.display_number is None


async def test_a_shadow_report_is_compared_recorded_and_listed(Session, alerts):
    from app.api.v1.pos_counter import counter_sync_overview
    from app.schemas.pos_counter import CounterShadowReport
    from app.services.pos import counter_bundle_service
    from app.services.pos import counter_pricing as cp

    world = await _world(Session)
    async with Session() as db:
        branch = await db.get(Branch, world.branch_id)
        cashier = await db.get(User, world.cashier_id)
        till = await db.get(Till, world.till_id)
        order = await pos_order_service.open_order(
            db, branch=branch, user=cashier, till=till
        )
        await pos_order_service.add_item(
            db, order=order, user=cashier, product_id=world.brownie_id, quantity=2
        )
        order = await pos_order_service.get_order(db, order.id)
        device = await db.get(Device, world.device_id)
        served = await counter_bundle_service.serve(
            db, device=device, build_number="999999"
        )
        ctx = counter_bundle_service.context_from_payload(served.body)
        pricing = cp.price_check(
            ctx,
            [
                cp.CheckLine(
                    id=item.id,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    unit_price=Decimal(str(item.base_price)),
                )
                for item in order.items
            ],
            datetime.now(timezone.utc),
        )
        await db.commit()
    wire = cp.pricing_to_wire(pricing)
    report = {
        "order_id": str(order.id),
        "bundle_hash": served.hash,
        "engine_version": 1,
        "totals": {
            k: wire[k]
            for k in (
                "subtotal",
                "discount_total",
                "tax_total",
                "total_excl_tax",
                "rounding",
                "total",
            )
        }
        | {"taxes": wire["taxes"]},
        "lines": [
            {"id": line["id"], "totals": {k: v for k, v in line.items() if k != "id"}}
            for line in wire["lines"]
        ],
    }

    async with Session() as db:
        device = await db.get(Device, world.device_id)
        same = await counter_ingest_service.shadow_compare(
            db, device=device, report=CounterShadowReport.model_validate(report)
        )
        await db.commit()
    assert same == []

    report["totals"]["total"] = "41.00"
    async with Session() as db:
        device = await db.get(Device, world.device_id)
        differ = await counter_ingest_service.shadow_compare(
            db, device=device, report=CounterShadowReport.model_validate(report)
        )
        await db.commit()
    assert {d["field"] for d in differ} == {"total"}
    assert len(alerts) == 1
    flagged = await _order(Session, order.id)
    assert "shadow_mismatch" in flagged.ingest_flags
    assert flagged.pricing_audit["shadow"]["differences"] == differ
    assert flagged.total == Decimal("42.00"), "shadow never touches the sale"

    async with Session() as db:
        overview = await counter_sync_overview(
            branch_id=world.branch_id, include_resolved=False, limit=50, db=db, _=None
        )
    assert [o.id for o in overview.orders] == [order.id]
    assert [d.device_id for d in overview.devices] == [world.device_id]
