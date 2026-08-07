"""
Which email an order earns, and what is in it.

Three places move an order — the admin's status endpoint, Stripe's webhooks and
the couriers' — and each of them used to decide independently which emails
existed. That is why a courier marking an order delivered notified nobody. The
decision lives in `email_service.notify_status_change` now, so it is testable
once rather than in three shapes.
"""

from __future__ import annotations

import html as html_lib
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.schemas.fulfilment import FulfilmentResponse, PickupBranchResponse
from app.schemas.order import OrderItemResponse, OrderResponse
from app.services import email_service

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

BRANCH = PickupBranchResponse(
    id=uuid.uuid4(),
    reference="K001",
    name="Melting Moments Cakes",
    name_ar="ملتينج مومنتس كيكس",
    address="Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah",
    address_ar="جاردن تاور 1 محل رقم 1، المجاز 3، الشارقة",
    city="Sharjah",
    city_ar="الشارقة",
    phone="06 552 1234",
    latitude=25.3304139,
    longitude=55.3736131,
    maps_url="https://www.google.com/maps/search/?api=1&query=25.3304139,55.3736131",
    opening_from="09:00",
    opening_to="23:00",
)


def _order(
    *,
    status: OrderStatusEnum = OrderStatusEnum.CONFIRMED,
    method: DeliveryMethodEnum = DeliveryMethodEnum.DELIVERY,
    stage: str = "preparing",
    estimated_at: datetime | None = None,
    precision: str | None = "time",
    tracking_url: str | None = None,
    tracking_by_sms: bool = False,
    courier_managed: bool = False,
    branch: PickupBranchResponse | None = None,
    payment_id: str | None = "pi_test",
    payment_method: str | None = "stripe",
    source: str | None = "online",
    **stamps,
) -> OrderResponse:
    return OrderResponse(
        id=uuid.uuid4(),
        order_number="MM-20260804-014",
        user_id=None,
        email="customer@example.com",
        delivery_method=method,
        delivery_fee=15.0,
        subtotal=250.0,
        discount_amount=25.0,
        total=240.0,
        status=status,
        promo_code_used="WELCOME10",
        shipping_address_snapshot={
            "first_name": "Layla",
            "last_name": "Al Nuaimi",
            "address_line_1": "Villa 14, Al Majaz 3",
            "city": "Sharjah",
            "phone": "+971501234567",
        },
        payment_method=payment_method,
        payment_provider="stripe",
        payment_id=payment_id,
        vat_rate=0.05,
        vat_amount=11.43,
        total_excl_vat=228.57,
        notes="Happy Birthday Sara",
        admin_notes=None,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
        source=source,
        items=[
            OrderItemResponse(
                id=uuid.uuid4(),
                product_id=uuid.uuid4(),
                product_name="Belgian Chocolate Brownie Box",
                product_sku="BRW-12",
                quantity=2,
                base_price=110.0,
                options_price=15.0,
                unit_price=125.0,
                total_price=250.0,
                selected_options_snapshot=[{"option_name": "12 pieces"}],
            )
        ],
        fulfilment=FulfilmentResponse(
            method=method.value,
            stage=stage,
            estimated_at=estimated_at,
            precision=precision,
            tracking_url=tracking_url,
            tracking_by_sms=tracking_by_sms,
            courier_managed=courier_managed,
            branch=branch,
            **stamps,
        ),
    )


def body(message: dict) -> str:
    """
    The message as a reader sees it.

    Two things stand between the source and the copy. Jinja autoescapes, so
    every apostrophe and ampersand that arrived through a variable is `&#39;`
    and `&amp;`; and a sentence in a template is wrapped across lines with
    indentation, so it contains whitespace a reader never sees. Asserting
    against the raw HTML means asserting against both of those rather than
    against the copy, and the copy is the thing under test.
    """
    return re.sub(r"\s+", " ", html_lib.unescape(message["html"]))


@pytest.fixture
def sent(monkeypatch):
    """Capture what would have gone to Resend, and never touch the database."""
    outbox: list[dict] = []

    def _fake_send(to, subject, html):
        outbox.append({"to": to, "subject": subject, "html": html})
        return {"status": "sent", "resend_id": "re_test", "error": None}

    async def _no_log(*_args, **_kwargs):
        return None

    monkeypatch.setattr(email_service, "_send", _fake_send)
    monkeypatch.setattr(email_service, "_log", _no_log)
    return outbox


# ── which email ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,expected",
    [
        (OrderStatusEnum.CONFIRMED, "confirmed"),
        (OrderStatusEnum.OUT_FOR_DELIVERY, "out_for_delivery"),
        (OrderStatusEnum.DELIVERED, "delivered"),
        (OrderStatusEnum.CANCELLED, "cancelled"),
        (OrderStatusEnum.PAYMENT_FAILED, "payment_failed"),
        (OrderStatusEnum.REFUNDED, "refunded"),
        # Deliberately silent: the checkout page itself answers `created`, and a
        # dispute is a conversation with a bank rather than with a customer.
        (OrderStatusEnum.CREATED, None),
        (OrderStatusEnum.DISPUTED, None),
    ],
)
async def test_every_status_sends_exactly_the_email_it_earns(status, expected, sent):
    acted = await email_service.notify_status_change(_order(status=status))
    assert acted == expected
    assert len(sent) == (1 if expected else 0)


@pytest.mark.asyncio
async def test_packed_is_the_come_and_get_it_email_for_a_collection_order(sent):
    order = _order(
        status=OrderStatusEnum.PACKED,
        method=DeliveryMethodEnum.PICKUP,
        stage="ready",
        estimated_at=NOW,
        precision="exact",
        branch=BRANCH,
    )
    assert email_service.should_send_packed(order) is True
    assert await email_service.notify_status_change(order) == "packed"
    assert "Ready to collect" in sent[0]["subject"]


@pytest.mark.asyncio
async def test_packed_is_the_last_word_for_a_third_party_delivery(sent):
    """
    Nobody will tell us anything after this, so it is the last email the
    customer would ever get — which is exactly why it has to be sent.
    """
    order = _order(status=OrderStatusEnum.PACKED, stage="ready", courier_managed=False)
    assert email_service.should_send_packed(order) is True
    assert await email_service.notify_status_change(order) == "packed"
    assert "On its way" in sent[0]["subject"]


@pytest.mark.asyncio
async def test_packed_says_nothing_when_a_rider_will_be_the_news(sent):
    """
    For a courier we book, "packed" means the box is on a shelf. The email worth
    having is the next one, when somebody is actually carrying it — and sending
    both turns the useful one into the second of two.
    """
    order = _order(status=OrderStatusEnum.PACKED, stage="ready", courier_managed=True)
    assert email_service.should_send_packed(order) is False
    assert await email_service.notify_status_change(order) is None
    assert sent == []


@pytest.mark.asyncio
async def test_a_failed_handover_is_its_own_status(sent):
    """
    A rider who could not hand the parcel over used to leave the order reading
    `out_for_delivery`, so the mailer had to check the courier record ahead of
    the status map to avoid telling somebody their cake was on its way minutes
    after they watched a driver leave. It is a status now, so it is in the map
    with everything else and there is one path rather than two.
    """
    order = _order(
        status=OrderStatusEnum.UNDELIVERED,
        stage="undelivered",
        estimated_at=None,
        precision=None,
        courier_managed=True,
    )
    assert await email_service.notify_status_change(order) == "undelivered"
    assert "couldn't deliver" in sent[0]["subject"]
    assert "wasn't able to complete the delivery" in body(sent[0])
    assert len(sent) == 1, "one status, one email"


# ── what is in it ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_collection_email_carries_the_branch_and_its_map_link(sent):
    await email_service.send_order_packed(
        _order(
            status=OrderStatusEnum.PACKED,
            method=DeliveryMethodEnum.PICKUP,
            stage="ready",
            estimated_at=NOW,
            precision="exact",
            branch=BRANCH,
        )
    )
    html = body(sent[0])
    assert "Melting Moments Cakes" in html
    assert "Garden Tower 1 Shop no 1, Al Majaz 3, Sharjah" in html
    assert "Sharjah" in html
    assert BRANCH.maps_url in html
    assert "09:00" in html and "23:00" in html
    # One language, not both. This email is English because the order was placed
    # in English; the Arabic card is asserted in `test_email_locales`. It used to
    # stack the two, because an order recorded no locale and the email had to
    # hedge — `orders.locale` is what removed the need to.
    assert "ملتينج مومنتس كيكس" not in html


@pytest.mark.asyncio
async def test_the_out_for_delivery_email_leads_with_the_live_link(sent):
    await email_service.send_order_out_for_delivery(
        _order(
            status=OrderStatusEnum.OUT_FOR_DELIVERY,
            stage="on_the_way",
            estimated_at=NOW + timedelta(minutes=45),
            tracking_url="https://share.lalamove.com/?SG100250001",
            courier_managed=True,
            picked_up_at=NOW,
        )
    )
    html = body(sent[0])
    assert "https://share.lalamove.com/?SG100250001" in html
    assert "Track live" in html
    # "Arriving" became "Estimated delivery" — the same panel, worded as an
    # estimate rather than as an appointment.
    assert "Estimated delivery" in html


@pytest.mark.asyncio
async def test_an_order_email_never_names_the_courier(sent):
    """
    The storefront rule, enforced at the last place it could leak. The link says
    "track live", not "track your Lalamove booking".
    """
    await email_service.send_order_out_for_delivery(
        _order(
            status=OrderStatusEnum.OUT_FOR_DELIVERY,
            stage="on_the_way",
            estimated_at=NOW + timedelta(minutes=45),
            courier_managed=True,
            picked_up_at=NOW,
        )
    )
    text = body(sent[0]).lower()
    for word in ("lalamove", "noon send", "noon_send", "courier", "rider on demand"):
        assert word not in text


@pytest.mark.asyncio
async def test_a_delivery_with_no_estimate_shows_no_panel_rather_than_a_guess(sent):
    """
    Nothing to promise is a real answer, and an empty panel beats a made-up
    time on an email somebody is planning their afternoon around.
    """
    await email_service.send_order_cancelled(
        _order(
            status=OrderStatusEnum.CANCELLED,
            stage="settled",
            estimated_at=None,
            precision=None,
        )
    )
    html = body(sent[0])
    assert "Estimated delivery" not in html
    assert "Order cancelled" in html


@pytest.mark.asyncio
async def test_a_cancellation_only_mentions_a_refund_when_money_was_taken(sent):
    """Promising a refund on an unpaid order generates a support email a week later."""
    await email_service.send_order_cancelled(
        _order(status=OrderStatusEnum.CANCELLED, stage="settled", estimated_at=None)
    )
    assert "Your refund" in body(sent[0])

    sent.clear()
    await email_service.send_order_cancelled(
        _order(
            status=OrderStatusEnum.CANCELLED,
            stage="settled",
            estimated_at=None,
            payment_id=None,
        )
    )
    assert "Your refund" not in body(sent[0])


@pytest.mark.asyncio
async def test_a_cash_collection_order_is_not_promised_a_card_refund(sent):
    await email_service.send_order_cancelled(
        _order(
            status=OrderStatusEnum.CANCELLED,
            method=DeliveryMethodEnum.PICKUP,
            stage="settled",
            estimated_at=None,
            payment_method="cod",
        )
    )
    assert "Your refund" not in body(sent[0])


@pytest.mark.asyncio
async def test_a_collection_timeline_has_no_delivery_step(sent):
    """
    Greying out a stage nobody is waiting for reads as a stage that was skipped.
    """
    await email_service.send_order_packed(
        _order(
            status=OrderStatusEnum.PACKED,
            method=DeliveryMethodEnum.PICKUP,
            stage="ready",
            estimated_at=NOW,
            precision="exact",
            branch=BRANCH,
        )
    )
    html = body(sent[0])
    assert "Ready to collect" in html
    assert "Out for delivery" not in html


@pytest.mark.asyncio
async def test_a_render_failure_is_logged_against_the_order_not_raised(monkeypatch):
    """
    A cake that is baked and boxed must not be blocked by a typo in a Jinja
    file — and a courier webhook must not be rejected because we could not write
    about it. Lalamove would retry the whole event for a day.
    """
    logged: list[tuple] = []

    async def _capture(template, recipient, subject, result, order_number=None):
        logged.append((template, result["status"], order_number))

    monkeypatch.setattr(email_service, "_log", _capture)
    monkeypatch.setattr(
        email_service,
        "_render",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad template")),
    )

    await email_service.send_order_confirmation(_order())

    assert logged == [("order_confirmation", "failed", "MM-20260804-014")]


@pytest.mark.asyncio
async def test_every_template_renders_for_the_status_that_uses_it(sent):
    """
    A smoke test across the whole matrix. Each of these is one production email
    nobody would see fail until it did not arrive.
    """
    cases = [
        (email_service.send_order_confirmation, OrderStatusEnum.CONFIRMED, {}),
        (
            email_service.send_order_packed,
            OrderStatusEnum.PACKED,
            {"stage": "ready", "method": DeliveryMethodEnum.PICKUP, "branch": BRANCH},
        ),
        (
            email_service.send_order_out_for_delivery,
            OrderStatusEnum.OUT_FOR_DELIVERY,
            {"stage": "on_the_way", "courier_managed": True, "picked_up_at": NOW},
        ),
        (
            email_service.send_order_delivered,
            OrderStatusEnum.DELIVERED,
            {"stage": "delivered", "precision": "exact", "delivered_at": NOW},
        ),
        (
            email_service.send_order_undelivered,
            OrderStatusEnum.OUT_FOR_DELIVERY,
            {"stage": "undelivered", "estimated_at": None, "precision": None},
        ),
        (
            email_service.send_order_cancelled,
            OrderStatusEnum.CANCELLED,
            {"stage": "settled", "estimated_at": None, "precision": None},
        ),
        (
            email_service.send_refund_notification,
            OrderStatusEnum.REFUNDED,
            {"stage": "settled", "estimated_at": None, "precision": None},
        ),
        (
            email_service.send_payment_failed,
            OrderStatusEnum.PAYMENT_FAILED,
            {"stage": "settled", "estimated_at": None, "precision": None},
        ),
        (email_service.send_owner_order_notification, OrderStatusEnum.CONFIRMED, {}),
    ]
    for send, status, extra in cases:
        extra.setdefault("estimated_at", NOW + timedelta(hours=2))
        await send(_order(status=status, **extra))

    # The owner notification goes to both owners, so one more than the calls.
    assert len(sent) == len(cases) + 1
    for message in sent:
        assert "<!DOCTYPE html>" in message["html"]
        assert "MELTING MOMENTS" in message["html"].upper()
        assert message["subject"].endswith("Melting Moments")


# ── counter sales ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        OrderStatusEnum.CONFIRMED,
        OrderStatusEnum.PACKED,
        OrderStatusEnum.OUT_FOR_DELIVERY,
        OrderStatusEnum.DELIVERED,
        OrderStatusEnum.CANCELLED,
        OrderStatusEnum.PAYMENT_FAILED,
        OrderStatusEnum.REFUNDED,
    ],
)
async def test_a_counter_sale_sends_nothing_whatever_its_status(status, sent):
    """
    The customer is at the counter holding the box and a printed receipt.
    "Your order is confirmed" tells them something they watched happen.
    """
    order = _order(status=status, source="cashier")
    assert await email_service.notify_status_change(order) is None
    assert sent == []


@pytest.mark.asyncio
async def test_a_counter_sale_is_turned_away_at_the_mailer_not_at_its_callers(sent):
    """
    Called directly, past `notify_status_change`, because `payment_service` does
    exactly that. The rule has to live where every caller passes.
    """
    counter = _order(source="cashier")
    await email_service.send_order_confirmation(counter)
    await email_service.send_order_packed(counter)
    await email_service.send_order_delivered(counter)
    await email_service.send_order_cancelled(counter)
    await email_service.send_payment_failed(counter)
    await email_service.send_refund_notification(counter)
    await email_service.send_owner_order_notification(counter)
    assert sent == []


@pytest.mark.asyncio
async def test_the_counter_does_not_need_telling_about_the_counter(sent):
    """The owner notification is for website orders, not for every till sale."""
    await email_service.send_owner_order_notification(_order(source="cashier"))
    assert sent == []

    await email_service.send_owner_order_notification(_order(source="online"))
    # One per owner.
    assert len(sent) == len(email_service.OWNER_ORDER_RECIPIENTS)


def test_the_gate_reads_the_channel_and_not_whether_it_reached_a_register():
    """
    The regression this whole rule is one edit away from.

    `is_pos` is **true for a website order** — a storefront order is attached to
    a branch's register and is a POS order in every operational sense. Gating on
    it instead of on `source` would silence every customer email the shop sends,
    and nothing else in the suite would fail. `source` is the column that exists
    for this distinction, and the one the admin's channel tab already filters on.
    """
    assert email_service.is_counter_sale(_order(source="cashier")) is True
    assert email_service.is_counter_sale(_order(source="online")) is False
    # A response built by hand carries no channel. It fails open on purpose: a
    # stray email to a counter customer is a small cost, and silencing the
    # storefront is not.
    assert email_service.is_counter_sale(_order(source=None)) is False


# ── tracking that arrives somewhere else ──────────────────────────────────────


@pytest.mark.asyncio
async def test_a_courier_that_texts_the_link_says_so(sent):
    """
    noon Send message the customer their own tracking link once a rider has the
    box, and give us nothing to link to. Before this the email simply showed no
    live tracking and offered no explanation, which reads as something broken
    rather than something arriving by another route.
    """
    order = _order(
        status=OrderStatusEnum.OUT_FOR_DELIVERY,
        stage="on_the_way",
        courier_managed=True,
        tracking_by_sms=True,
    )
    assert await email_service.notify_status_change(order) == "out_for_delivery"
    assert "text you a live tracking link" in body(sent[0])
    assert "check your messages" in body(sent[0])


@pytest.mark.asyncio
async def test_a_courier_that_gives_us_a_map_does_not_send_them_to_their_texts(sent):
    """A live link and a "look at your phone" note would be two answers."""
    order = _order(
        status=OrderStatusEnum.OUT_FOR_DELIVERY,
        stage="on_the_way",
        courier_managed=True,
        tracking_url="https://track.example/abc",
        tracking_by_sms=True,
    )
    await email_service.notify_status_change(order)
    assert "Track live" in body(sent[0])
    assert "check your messages" not in body(sent[0])


@pytest.mark.asyncio
async def test_nothing_is_promised_when_no_message_is_coming(sent):
    order = _order(
        status=OrderStatusEnum.OUT_FOR_DELIVERY,
        stage="on_the_way",
        courier_managed=True,
    )
    await email_service.notify_status_change(order)
    assert "check your messages" not in body(sent[0])


@pytest.mark.asyncio
async def test_the_note_is_written_in_arabic_for_an_arabic_order(sent):
    order = _order(
        status=OrderStatusEnum.OUT_FOR_DELIVERY,
        stage="on_the_way",
        courier_managed=True,
        tracking_by_sms=True,
    )
    order.locale = "ar"
    await email_service.notify_status_change(order)
    assert "رابط تتبع مباشر" in body(sent[0])
    assert "text you a live tracking link" not in body(sent[0])
