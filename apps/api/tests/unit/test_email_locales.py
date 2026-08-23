"""
An email is written in the language the order was placed in.

Nothing on an order used to record that, so every email was English — including
to a customer who browsed, read the checkout and paid entirely in Arabic. The
locale is captured at checkout now, and everything the shop writes about the
order follows it.
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
from app.services.email_copy import STRINGS, translator
from app.services.orders.order_service import normalise_locale

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

BRANCH = PickupBranchResponse(
    id=uuid.uuid4(),
    reference="K001",
    name="Melting Moments Cakes",
    name_ar="ملتينج مومنتس كيكس",
    address="Garden Tower 1, Al Majaz 3, Sharjah",
    address_ar="جاردن تاور 1، المجاز 3، الشارقة",
    city="Sharjah",
    city_ar="الشارقة",
    phone="06 552 1234",
    latitude=25.33,
    longitude=55.37,
    maps_url="https://maps.example/pin",
    opening_from="09:00",
    opening_to="23:00",
)


def _order(
    *,
    locale: str = "en",
    status: OrderStatusEnum = OrderStatusEnum.CONFIRMED,
    method: DeliveryMethodEnum = DeliveryMethodEnum.PICKUP,
    stage: str = "preparing",
    branch: PickupBranchResponse | None = BRANCH,
) -> OrderResponse:
    return OrderResponse(
        id=uuid.uuid4(),
        order_number="MM-20260804-014",
        user_id=None,
        email="customer@example.com",
        delivery_method=method,
        delivery_fee=0.0,
        subtotal=250.0,
        discount_amount=0.0,
        total=250.0,
        status=status,
        promo_code_used=None,
        shipping_address_snapshot={"first_name": "Layla", "phone": "+971501234567"},
        payment_method="stripe",
        payment_provider="stripe",
        payment_id="pi_test",
        vat_rate=0.05,
        vat_amount=11.9,
        total_excl_vat=238.1,
        notes=None,
        admin_notes=None,
        created_at=NOW - timedelta(hours=1),
        updated_at=NOW,
        source="online",
        locale=locale,
        items=[
            OrderItemResponse(
                id=uuid.uuid4(),
                product_id=uuid.uuid4(),
                product_name="Brownie Box",
                product_sku="BRW",
                quantity=2,
                base_price=110.0,
                options_price=15.0,
                unit_price=125.0,
                total_price=250.0,
                selected_options_snapshot=[],
            )
        ],
        fulfilment=FulfilmentResponse(
            method=method.value,
            stage=stage,
            estimated_at=NOW + timedelta(hours=2),
            precision="time",
            branch=branch,
        ),
    )


def body(message: dict) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(message["html"]))


def html_tag(message: dict) -> str:
    """
    The opening `<html …>` element, which is where the language actually lives.

    Asserted on specifically because `lang="ar"` also appears in the stylesheet
    — the Arabic rules are `[lang="ar"] .tagline { … }` — so a substring search
    over the whole document matches every email in either language. It read as
    a passing test and was checking nothing.
    """
    match = re.search(r"<html[^>]*>", message["html"])
    assert match, "no <html> element"
    return match.group(0)


@pytest.fixture
def sent(monkeypatch):
    outbox: list[dict] = []

    def _fake_send(to, subject, html):
        outbox.append({"to": to, "subject": subject, "html": html})
        return {"status": "sent", "resend_id": "re_test", "error": None}

    async def _no_log(*_a, **_k):
        return None

    monkeypatch.setattr(email_service, "_send", _fake_send)
    monkeypatch.setattr(email_service, "_log", _no_log)
    return outbox


# ── the copy itself ───────────────────────────────────────────────────────────


def test_both_languages_say_all_the_same_things():
    """
    The one assertion that keeps a raw `refunded.cta` out of somebody's inbox.
    `translator` falls back per key so a gap degrades to English rather than to
    a dotted identifier — this is what stops the gap existing at all.
    """
    en, ar = set(STRINGS["en"]), set(STRINGS["ar"])
    assert en - ar == set(), f"missing Arabic: {sorted(en - ar)}"
    assert ar - en == set(), f"Arabic-only keys: {sorted(ar - en)}"
    assert len(en) > 100


def test_no_string_is_left_untranslated():
    """
    A copied-and-not-translated Arabic value is worse than a missing one: the
    parity test above passes and the customer still reads English. Order
    numbers and the brand are the deliberate exceptions — see `email_copy`.
    """
    same = {
        key
        for key, value in STRINGS["en"].items()
        if STRINGS["ar"][key] == value and value.strip()
    }
    assert same == set(), f"identical in both languages: {sorted(same)}"


def test_a_missing_key_falls_back_to_english_rather_than_to_its_own_name():
    t = translator("ar")
    assert t("confirmation.cta") == STRINGS["ar"]["confirmation.cta"]
    assert t("totally.made.up") == "totally.made.up"

    # An unknown locale is English, not empty.
    assert translator("fr")("confirmation.cta") == STRINGS["en"]["confirmation.cta"]


def test_a_placeholder_the_caller_forgot_does_not_raise():
    """An email that reads slightly wrong still arrives; one that raises does not."""
    t = translator("en")
    assert "{name}" in t("confirmation.heading")


# ── which language ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        ("ar", "ar"),
        ("AR", "ar"),
        ("ar-AE", "ar"),
        ("ar_AE", "ar"),
        ("en", "en"),
        ("en-GB", "en"),
        # Not a language the shop writes in, and not a reason to refuse an order.
        ("fr", "en"),
        ("", "en"),
        (None, "en"),
    ],
)
def test_the_checkout_locale_is_normalised_rather_than_trusted(value, expected):
    assert normalise_locale(value) == expected


def test_an_order_from_before_the_column_existed_is_english():
    assert email_service.locale_of(_order(locale="en")) == "en"
    assert email_service.locale_of(_order(locale="ar")) == "ar"


# ── what actually goes out ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_arabic_order_is_written_in_arabic_subject_included(sent):
    await email_service.send_order_confirmation(_order(locale="ar"))

    assert STRINGS["ar"]["confirmation.subject"] in sent[0]["subject"]
    # The order number stays Latin — it is what a customer reads back on the
    # phone and searches their inbox for.
    assert "MM-20260804-014" in sent[0]["subject"]

    tag = html_tag(sent[0])
    assert 'lang="ar"' in tag
    assert 'dir="rtl"' in tag

    html = body(sent[0])
    assert STRINGS["ar"]["confirmation.lead"] in html
    assert STRINGS["en"]["confirmation.lead"] not in html


@pytest.mark.asyncio
async def test_an_english_order_is_unchanged(sent):
    await email_service.send_order_confirmation(_order(locale="en"))
    tag = html_tag(sent[0])
    assert 'lang="en"' in tag
    assert 'dir="ltr"' in tag
    assert 'dir="rtl"' not in tag
    assert STRINGS["en"]["confirmation.lead"] in body(sent[0])


@pytest.mark.asyncio
async def test_the_branch_card_speaks_one_language_not_both(sent):
    """
    It used to stack the English and the Arabic, because the email had no locale
    and had to hedge. Now it knows, so it says it once.
    """
    await email_service.send_order_confirmation(_order(locale="ar"))
    html = body(sent[0])
    assert BRANCH.name_ar in html
    assert BRANCH.address_ar in html
    assert BRANCH.city_ar in html
    assert BRANCH.name not in html
    assert BRANCH.address not in html


@pytest.mark.asyncio
async def test_the_links_land_on_a_page_in_the_same_language(sent):
    """An Arabic email whose one button opens an English page has only moved the
    problem down a click."""
    await email_service.send_order_confirmation(_order(locale="ar"))
    html = body(sent[0])
    assert "/ar/track?" in html
    assert "/en/track?" not in html


@pytest.mark.asyncio
async def test_prices_and_the_order_number_stay_latin_in_arabic(sent):
    """
    Eastern Arabic numerals in a total somebody has to match against a bank
    statement help nobody, and the storefront writes prices this way too.
    """
    await email_service.send_order_confirmation(_order(locale="ar"))
    html = body(sent[0])
    assert "AED 250.00" in html
    assert "MM-20260804-014" in html


@pytest.mark.asyncio
async def test_a_latin_run_inside_arabic_is_marked_so_it_is_not_reordered(sent):
    """
    Bidi reorders a Latin run inside an RTL paragraph: a phone number rendered
    "1234 552 06" and a quantity line "AED 95.00 × 2". Anything always-Latin is
    an explicit `ltr` island; anything that could be either is `auto`.
    """
    raw = None
    await email_service.send_order_confirmation(_order(locale="ar"))
    raw = sent[0]["html"]
    assert 'dir="ltr"' in raw
    assert 'dir="auto"' in raw


@pytest.mark.asyncio
async def test_the_owner_notification_stays_english_whatever_the_customer_read(sent):
    """
    It is an internal email to the two people who run the shop, and it links
    into an English-only admin.
    """
    await email_service.send_owner_order_notification(_order(locale="ar"))
    assert "New order" in sent[0]["subject"]
    assert 'lang="en"' in html_tag(sent[0])


@pytest.mark.asyncio
async def test_the_account_emails_take_the_language_of_the_request(sent):
    """No order to read a locale off, so the endpoint passes the one it saw."""
    await email_service.send_welcome("customer@example.com", "ar")
    assert sent[0]["subject"] == STRINGS["ar"]["welcome.subject"]
    assert 'dir="rtl"' in html_tag(sent[0])

    sent.clear()
    await email_service.send_password_reset("customer@example.com", "tok", "ar")
    assert STRINGS["ar"]["reset.subject"] in sent[0]["subject"]
    # And the link goes to the Arabic reset page.
    assert "/ar/reset-password?token=tok" in body(sent[0])

    sent.clear()
    await email_service.send_welcome("customer@example.com")
    assert sent[0]["subject"] == STRINGS["en"]["welcome.subject"]


@pytest.mark.asyncio
async def test_every_template_renders_in_arabic(sent):
    """
    A smoke pass over the whole set. Each is one production email that would
    fail only where nobody is looking.
    """
    cases = [
        (email_service.send_order_confirmation, {}),
        (
            email_service.send_order_packed,
            {"status": OrderStatusEnum.PACKED, "stage": "ready"},
        ),
        (
            email_service.send_order_out_for_delivery,
            {
                "status": OrderStatusEnum.OUT_FOR_DELIVERY,
                "method": DeliveryMethodEnum.DELIVERY,
                "stage": "on_the_way",
                "branch": None,
            },
        ),
        (
            email_service.send_order_delivered,
            {"status": OrderStatusEnum.DELIVERED, "stage": "collected"},
        ),
        (email_service.send_order_undelivered, {"stage": "undelivered"}),
        (
            email_service.send_order_cancelled,
            {"status": OrderStatusEnum.CANCELLED, "stage": "settled"},
        ),
        (
            email_service.send_refund_notification,
            {"status": OrderStatusEnum.REFUNDED, "stage": "settled"},
        ),
        (
            email_service.send_payment_failed,
            {"status": OrderStatusEnum.PAYMENT_FAILED, "stage": "settled"},
        ),
    ]
    for send, extra in cases:
        await send(_order(locale="ar", **extra))

    assert len(sent) == len(cases)
    for message in sent:
        assert 'dir="rtl"' in html_tag(message), message["subject"]
        html = body(message)
        # No key leaked through as a raw identifier.
        assert not re.search(
            r"\b(confirmation|packed|otw|delivered|cancelled|"
            r"refunded|failed|undelivered|totals|estimate|step)\."
            r"[a-z_]+\b",
            html,
        ), message["subject"]


# ── dates ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "locale,delta,expected",
    [
        ("en", 0, "Today"),
        ("ar", 0, "اليوم"),
        ("en", 1, "Tomorrow"),
        ("ar", 1, "غداً"),
        ("en", -1, "Yesterday"),
        ("ar", -1, "أمس"),
    ],
)
def test_near_days_are_named_in_both_languages(locale, delta, expected):
    t = translator(locale)
    moment = NOW + timedelta(days=delta)
    rendered = email_service._when(moment, precision="day", now=NOW, t=t, locale=locale)
    assert rendered == expected


def test_a_further_date_is_an_arabic_weekday_and_month():
    """
    Written out rather than taken from the C library's Arabic locale, which is
    not installed in the container — so this is the test that it is right.
    """
    moment = datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc)
    rendered = email_service._when(
        moment, precision="day", now=NOW, t=translator("ar"), locale="ar"
    )
    # 20 August 2026 is a Thursday; 13:30 Gulf time is still the 20th.
    assert rendered == "الخميس 20 أغسطس"


def test_the_half_of_the_day_is_marked_in_arabic_letters():
    """ "AM" in an Arabic sentence reads as a foreign object."""
    morning = datetime(2026, 8, 4, 5, 30, tzinfo=timezone.utc)  # 09:30 Gulf
    evening = datetime(2026, 8, 4, 15, 30, tzinfo=timezone.utc)  # 19:30 Gulf
    t = translator("ar")
    assert email_service._when(
        morning, precision="time", now=NOW, t=t, locale="ar"
    ).endswith("9:30 ص")
    assert email_service._when(
        evening, precision="time", now=NOW, t=t, locale="ar"
    ).endswith("7:30 م")
