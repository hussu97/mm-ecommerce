"""Custom-order invoice: readiness, the rendered figures, the bank block, and
byte-identical PDFs.

Built from transient ORM objects with the loader stubbed, so nothing needs a
database. The PDF tests need WeasyPrint's Pango/GObject libraries, which the CI
Python runner does not carry (and a Mac only finds with
`DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`), so they skip where it cannot
import rather than turning the job red.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.exceptions import CustomOrderInvoiceUnavailable
from app.models.custom_order import CustomOrder
from app.models.legal_entity import LegalEntity
from app.models.order import Order, OrderItem, OrderStatusEnum
from app.models.pos_order import OrderCharge, OrderTax
from app.services import email_service
from app.services.orders import custom_order_invoice as inv
from app.services.orders.custom_order_invoice import (
    InvoiceSources,
    invoice_readiness,
    render_invoice_html,
)

ORDER_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _weasyprint_available() -> bool:
    try:
        from weasyprint import HTML  # noqa: F401
    except Exception:
        return False
    return True


needs_weasyprint = pytest.mark.skipif(
    not _weasyprint_available(), reason="WeasyPrint/Pango not available"
)


def _order(**overrides) -> Order:
    fields = dict(
        id=ORDER_ID,
        order_number="CO-20260926-001",
        source="custom",
        status=OrderStatusEnum.PACKED,
        customer_name="Aisha Rahman",
        email="aisha@example.com",
        customer_phone="+971501234567",
        shipping_address_snapshot={
            "address_line_1": "Al Majaz 2, Corniche Street",
            "unit_number": "Villa 12",
            "first_name": "Aisha",
            "last_name": "Rahman",
            "phone": "+971501234567",
        },
        legal_entity_id=uuid.uuid4(),
        promised_at=datetime(2026, 9, 27, 8, 0, tzinfo=UTC),
        delivered_at=None,
        created_at=datetime(2026, 9, 20, 9, 0, tzinfo=UTC),
        subtotal=Decimal("1050.00"),
        discount_amount=Decimal("0.00"),
        charges_amount=Decimal("32.47"),
        rounding_amount=Decimal("0.00"),
        vat_amount=Decimal("51.55"),
        total_excl_vat=Decimal("1030.92"),
        total=Decimal("1082.47"),
    )
    fields.update(overrides)
    order = Order(**fields)
    order.items = [
        OrderItem(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            created_at=datetime(2026, 9, 20, 9, 0, 1, tzinfo=UTC),
            product_name="Three-tier floral wedding cake",
            product_sku="FG0119",
            quantity=1,
            base_price=Decimal("900.00"),
            unit_price=Decimal("900.00"),
            total_price=Decimal("900.00"),
            tax_amount=Decimal("42.86"),
            tax_exclusive_total_price=Decimal("857.14"),
        ),
        OrderItem(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            created_at=datetime(2026, 9, 20, 9, 0, 2, tzinfo=UTC),
            product_name="Matching cupcakes",
            product_sku="FG0119",
            quantity=12,
            base_price=Decimal("12.50"),
            unit_price=Decimal("12.50"),
            total_price=Decimal("150.00"),
            tax_amount=Decimal("7.14"),
            tax_exclusive_total_price=Decimal("142.86"),
        ),
    ]
    order.order_charges = [
        OrderCharge(
            name="Card payment fee",
            type="fixed",
            amount=Decimal("32.47"),
            tax_amount=Decimal("1.55"),
            tax_exclusive_amount=Decimal("30.92"),
        )
    ]
    order.order_taxes = [
        OrderTax(
            name="VAT",
            rate=Decimal("0.0500"),
            taxable_amount=Decimal("1030.92"),
            amount=Decimal("51.55"),
        )
    ]
    return order


def _entity(**overrides) -> LegalEntity:
    fields = dict(
        reference="fatema",
        legal_name="Fatema Cake Sweets",
        brand_name="Melting Moments Cakes",
        vat_registered=True,
        tax_number="100000000000003",
        invoice_title="Tax Invoice",
        trade_license_number="SHJ-12345",
        logo_url=None,
        registered_address="Shop 4, Al Qasba\nSharjah, UAE",
        bank_name="Emirates NBD",
        bank_account_name="Fatema Cake Sweets",
        bank_account_number="1012345678901",
        iban="AE070331234567890123456",
        swift_code="EBILAEAD",
        invoice_cc_emails=["owner-a@example.com", "owner-b@example.com"],
    )
    fields.update(overrides)
    return LegalEntity(**fields)


def _sources(payment_type: str | None = "card", **order_overrides) -> InvoiceSources:
    order = _order(**order_overrides)
    custom = CustomOrder(
        order_id=order.id,
        payment_type=payment_type,
        card_fee_mode="separate_line" if payment_type == "card" else None,
        created_via="admin",
    )
    return InvoiceSources(
        order=order, custom_order=custom, entity=_entity(), logo_data_uri=None
    )


# ── readiness ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "status",
    ["packed", "out_for_delivery", "delivered", "undelivered"],
)
def test_ready_from_packed_onwards(status):
    assert invoice_readiness(_order(status=OrderStatusEnum(status))) is None


def test_ready_accepts_a_plain_string_status():
    assert invoice_readiness(_order(status="delivered")) is None


@pytest.mark.parametrize("status", ["created", "confirmed", "arrived_at_pos"])
def test_not_ready_before_packing(status):
    assert (
        invoice_readiness(_order(status=OrderStatusEnum(status)))
        == "The order has not been packed yet."
    )


def test_not_ready_when_cancelled():
    assert (
        invoice_readiness(_order(status=OrderStatusEnum.CANCELLED))
        == "The order is cancelled."
    )


def test_names_a_missing_customer_name():
    assert (
        invoice_readiness(_order(customer_name="  "))
        == "The customer's name is missing."
    )


def test_names_a_missing_customer_email():
    assert invoice_readiness(_order(email="")) == "The customer's email is missing."


def test_names_both_when_name_and_email_are_missing():
    assert (
        invoice_readiness(_order(customer_name=None, email=""))
        == "The customer's name and email are missing."
    )


def test_reports_status_and_missing_fields_together():
    reason = invoice_readiness(_order(status=OrderStatusEnum.CONFIRMED, email=""))
    assert reason == (
        "The order has not been packed yet. The customer's email is missing."
    )


def test_not_ready_without_a_legal_entity():
    assert (
        invoice_readiness(_order(legal_entity_id=None))
        == "The order has no legal entity to invoice under."
    )


def test_not_ready_for_another_channel():
    assert invoice_readiness(_order(source="web")) == (
        "Only custom orders are invoiced here."
    )


@pytest.mark.asyncio
async def test_render_raises_with_the_reason_when_not_ready():
    with pytest.raises(CustomOrderInvoiceUnavailable) as exc:
        await inv.render_invoice_pdf(None, _order(email=""))
    assert exc.value.detail == "The customer's email is missing."
    assert exc.value.status_code == 409
    assert exc.value.code == "custom_order_invoice_unavailable"


# ── rendered content ──────────────────────────────────────────────────────────


def test_money_shown_is_the_stored_figures():
    html = render_invoice_html(_sources())
    for figure in (
        "900.00",
        "857.14",
        "42.86",
        "12.50",
        "150.00",
        "142.86",
        "7.14",
        "32.47",
        "30.92",
        "1.55",
        "1,030.92",  # total excl. VAT, and VAT's taxable amount
        "51.55",
        "1,082.47",
    ):
        assert figure in html, figure
    assert "VAT 5% on 1,030.92" in html


def test_totals_are_not_recomputed_from_the_lines():
    # A stored total that the lines do not sum to is still what is printed.
    html = render_invoice_html(_sources(total=Decimal("1234.56")))
    assert "1,234.56" in html
    assert "1,082.47" not in html


def test_header_carries_the_entity_and_the_order():
    html = render_invoice_html(_sources())
    assert "Tax Invoice" in html
    assert "CO-20260926-001" in html
    assert "Fatema Cake Sweets" in html
    assert "Melting Moments Cakes" in html
    assert "TRN: 100000000000003" in html
    assert "Trade licence: SHJ-12345" in html
    assert "Al Qasba" in html


def test_date_of_supply_prefers_delivered_in_dubai_time():
    # 21:30 UTC on the 27th is 01:30 on the 28th in Dubai.
    html = render_invoice_html(
        _sources(delivered_at=datetime(2026, 9, 27, 21, 30, tzinfo=UTC))
    )
    assert "28 Sep 2026" in html
    assert 'content="2026-09-28T01:30:00+04:00"' in html


def test_date_of_supply_falls_back_to_promised():
    assert "27 Sep 2026" in render_invoice_html(_sources())


def test_bill_to_prints_customer_and_address():
    html = render_invoice_html(_sources())
    for text in (
        "Aisha Rahman",
        "aisha@example.com",
        "+971501234567",
        "Villa 12",
        "Al Majaz 2, Corniche Street",
    ):
        assert text in html


def test_bank_block_only_for_bank_transfer():
    bank = render_invoice_html(_sources(payment_type="bank_transfer"))
    assert "Pay by bank transfer" in bank
    assert "AE070331234567890123456" in bank
    assert "EBILAEAD" in bank
    assert "Bank transfer" in bank

    for payment_type in ("card", "cash", None):
        html = render_invoice_html(_sources(payment_type=payment_type))
        assert "Pay by bank transfer" not in html
        assert "AE070331234567890123456" not in html


def test_bank_block_omits_empty_fields():
    sources = _sources(payment_type="bank_transfer")
    sources.entity.swift_code = None
    sources.entity.bank_name = "  "
    html = render_invoice_html(sources)
    assert "SWIFT" not in html
    assert ">Bank<" not in html
    assert "IBAN" in html


# ── PDF ───────────────────────────────────────────────────────────────────────


@needs_weasyprint
@pytest.mark.asyncio
async def test_two_renders_are_byte_identical(monkeypatch):
    async def fake_load(db, order):
        return _sources(payment_type="bank_transfer")

    monkeypatch.setattr(inv, "_load_sources", fake_load)
    first = await inv.render_invoice_pdf(None, _order())
    second = await inv.render_invoice_pdf(None, _order())
    assert first[:5] == b"%PDF-"
    assert first == second
    assert ORDER_ID.hex.encode() in first


# ── send ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_attaches_the_pdf_and_copies_the_entity(monkeypatch):
    sources = _sources(payment_type="bank_transfer")

    async def fake_load(db, order):
        return sources

    async def fake_render(src):
        assert src is sources
        return b"%PDF-fake"

    sent = {}

    async def fake_send(recipient, subject, html, **kwargs):
        sent.update(recipient=recipient, subject=subject, html=html, **kwargs)
        return {"status": "sent", "resend_id": "re_1", "error": None}

    monkeypatch.setattr(inv, "_load_sources", fake_load)
    monkeypatch.setattr(inv, "_render", fake_render)
    monkeypatch.setattr(email_service, "send_with_attachment", fake_send)

    result = await inv.send_invoice(None, _order())

    assert result["status"] == "sent"
    assert sent["recipient"] == "aisha@example.com"
    assert sent["subject"] == "Tax Invoice CO-20260926-001 | Melting Moments Cakes"
    assert sent["filename"] == "CO-20260926-001.pdf"
    assert sent["content"] == b"%PDF-fake"
    assert sent["template"] == "custom_order_invoice"
    assert sent["cc"] == ["owner-a@example.com", "owner-b@example.com"]
    assert sent["order_number"] == "CO-20260926-001"
    assert "CO-20260926-001" in sent["html"]
    assert "AED 1,082.47" in sent["html"]
    assert "payment reference" in sent["html"]


@pytest.mark.asyncio
async def test_send_refuses_an_unready_order(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("must not send")

    monkeypatch.setattr(email_service, "send_with_attachment", boom)
    with pytest.raises(CustomOrderInvoiceUnavailable):
        await inv.send_invoice(None, _order(status=OrderStatusEnum.CANCELLED))
