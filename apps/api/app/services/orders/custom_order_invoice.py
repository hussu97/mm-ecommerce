"""A custom order's tax invoice: whether one may be issued, the PDF, the email.

The invoice number is the order number (`CO-YYYYMMDD-NNN`). The issuer is the
legal entity frozen onto the order (`orders.legal_entity_id`), so the name, TRN,
address, logo, title and bank details all come from that row and none is
written here. Every figure is a stored one — `order_items`, `order_charges`,
`order_taxes` and the order's totals — formatted, never recomputed, so the
invoice says exactly what the VAT return and the P&L read.

Rendering is Jinja2 → WeasyPrint, like `catalog/menu_pdf`, with the same bundled
fonts. It is deterministic: the same order, entity and logo bytes produce the
same PDF bytes. Nothing reads the clock; the PDF's `/ID` is the order id and its
created/modified dates are the date of supply; lines are sorted rather than
taken in database order. The one outside input is the logo, fetched before
layout and inlined as a data URI (`_logo_data_uri`), and cached per URL for the
life of the process — so a logo replaced at the same URL changes the PDF only
after a restart, and a logo that cannot be fetched renders the brand name in its
place.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import CustomOrderInvoiceUnavailable
from app.core.money import money, to_decimal
from app.core.trading_hours import DELIVERY_TIMEZONE
from app.models.custom_order import CustomOrder, CustomOrderPaymentTypeEnum
from app.models.legal_entity import LegalEntity
from app.models.order import Order, OrderStatusEnum
from app.services import email_service

__all__ = [
    "INVOICEABLE_STATUSES",
    "InvoiceSources",
    "invoice_readiness",
    "render_invoice_html",
    "render_invoice_pdf",
    "send_invoice",
]

logger = logging.getLogger(__name__)

#: Packed or later and not cancelled: the cake exists and is the customer's.
INVOICEABLE_STATUSES = frozenset(
    {
        OrderStatusEnum.PACKED.value,
        OrderStatusEnum.OUT_FOR_DELIVERY.value,
        OrderStatusEnum.DELIVERED.value,
        OrderStatusEnum.UNDELIVERED.value,
    }
)

_NOT_YET_PACKED = frozenset(
    {
        OrderStatusEnum.CREATED.value,
        OrderStatusEnum.CONFIRMED.value,
        OrderStatusEnum.ARRIVED_AT_POS.value,
    }
)

_STATUS_REASONS = {
    OrderStatusEnum.CANCELLED.value: "The order is cancelled.",
    OrderStatusEnum.REFUNDED.value: "The order has been refunded.",
    OrderStatusEnum.PAYMENT_FAILED.value: "The order's payment failed.",
    OrderStatusEnum.DISPUTED.value: "The order is disputed.",
}

_PAYMENT_LABELS = {
    CustomOrderPaymentTypeEnum.BANK_TRANSFER.value: "Bank transfer",
    CustomOrderPaymentTypeEnum.CARD.value: "Card",
    CustomOrderPaymentTypeEnum.CASH.value: "Cash",
}

_TZ = ZoneInfo(DELIVERY_TIMEZONE)
_CURRENCY = "AED"

_APP_DIR = Path(__file__).resolve().parents[2]
_TEMPLATES_DIR = _APP_DIR / "templates" / "invoices"
# The menu PDF's bundled OFL fonts, shared rather than copied.
_FONTS_DIR = _APP_DIR / "services" / "catalog" / "menu_pdf" / "fonts"

_LOGO_TIMEOUT_SECONDS = 5.0
#: Successful logo fetches, by URL. Failures are not cached, so the next render
#: retries.
_logo_cache: dict[str, str] = {}


# ─── Readiness ────────────────────────────────────────────────────────────────


def _status_value(order: Order) -> str:
    status = order.status
    return str(getattr(status, "value", status))


def invoice_readiness(order: Order) -> str | None:
    """None when this order may be invoiced, otherwise why not, for a person.

    Every problem is named, so the console can say "add the customer's email"
    rather than only that the button is disabled.
    """
    reasons: list[str] = []

    if order.source != "custom":
        reasons.append("Only custom orders are invoiced here.")

    status = _status_value(order)
    if status not in INVOICEABLE_STATUSES:
        if status in _NOT_YET_PACKED:
            reasons.append("The order has not been packed yet.")
        else:
            reasons.append(
                _STATUS_REASONS.get(
                    status, f"An order that is {status} cannot be invoiced."
                )
            )

    missing = []
    if not (order.customer_name or "").strip():
        missing.append("name")
    if not (order.email or "").strip():
        missing.append("email")
    if missing:
        verb = "are" if len(missing) > 1 else "is"
        reasons.append(f"The customer's {' and '.join(missing)} {verb} missing.")

    if order.legal_entity_id is None:
        reasons.append("The order has no legal entity to invoice under.")

    return " ".join(reasons) or None


def _require_ready(order: Order) -> None:
    reason = invoice_readiness(order)
    if reason is not None:
        raise CustomOrderInvoiceUnavailable(reason)


# ─── Loading ──────────────────────────────────────────────────────────────────


@dataclass
class InvoiceSources:
    """Everything the invoice prints, loaded before layout."""

    order: Order
    custom_order: CustomOrder | None
    entity: LegalEntity
    logo_data_uri: str | None


async def _logo_data_uri(url: str | None) -> str | None:
    """The logo at `url` as a data URI, or None when it cannot be fetched."""
    if not url:
        return None
    cached = _logo_cache.get(url)
    if cached is not None:
        return cached
    try:
        async with httpx.AsyncClient(
            timeout=_LOGO_TIMEOUT_SECONDS, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
    except Exception:
        logger.warning("invoice: logo fetch failed for %s", url)
        return None
    mime = resp.headers.get("content-type", "").split(";")[0].strip()
    if not mime.startswith("image/"):
        logger.warning("invoice: logo at %s is %r, not an image", url, mime)
        return None
    encoded = base64.b64encode(resp.content).decode("ascii")
    data_uri = f"data:{mime};base64,{encoded}"
    _logo_cache[url] = data_uri
    return data_uri


async def _load_sources(db: AsyncSession, order: Order) -> InvoiceSources:
    # `populate_existing`: the order is usually already in the identity map
    # without these collections, and would come back without them.
    stmt = (
        select(Order)
        .where(Order.id == order.id)
        .options(
            selectinload(Order.items),
            selectinload(Order.order_charges),
            selectinload(Order.order_taxes),
            selectinload(Order.legal_entity),
        )
        .execution_options(populate_existing=True)
    )
    loaded = (await db.execute(stmt)).scalars().one()
    entity = loaded.legal_entity
    if entity is None:
        raise CustomOrderInvoiceUnavailable(
            "The order has no legal entity to invoice under."
        )
    custom_order = await db.get(CustomOrder, loaded.id)
    return InvoiceSources(
        order=loaded,
        custom_order=custom_order,
        entity=entity,
        logo_data_uri=await _logo_data_uri(entity.logo_url),
    )


# ─── View model ───────────────────────────────────────────────────────────────


def _amount(value: Any) -> str:
    return f"{money(value):,.2f}"


def _rate_label(rate: Any) -> str:
    """A stored fraction (`0.0500`) as a label (`5%`)."""
    percent = money(to_decimal(rate) * 100).normalize()
    return f"{percent:f}%"


def _supply_at(order: Order) -> datetime | None:
    """Delivered, else promised, else created — never the clock."""
    for value in (order.delivered_at, order.promised_at, order.created_at):
        if value is not None:
            return value
    return None


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))
    return value.astimezone(_TZ)


def _sort_key(row: Any) -> tuple:
    created = getattr(row, "created_at", None)
    return (created.isoformat() if created else "", str(getattr(row, "id", "")))


def _address_lines(snapshot: Any) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    lines = []
    unit = (snapshot.get("unit_number") or "").strip()
    street = (snapshot.get("address_line_1") or "").strip()
    if unit:
        lines.append(unit)
    if street:
        lines.append(street)
    return lines


def _bank_details(entity: LegalEntity) -> list[tuple[str, str]]:
    fields = (
        ("Bank", entity.bank_name),
        ("Account name", entity.bank_account_name),
        ("Account number", entity.bank_account_number),
        ("IBAN", entity.iban),
        ("SWIFT", entity.swift_code),
    )
    return [(label, value.strip()) for label, value in fields if (value or "").strip()]


def build_invoice_context(sources: InvoiceSources) -> dict[str, Any]:
    """The template's inputs: strings already formatted from stored figures."""
    order, entity = sources.order, sources.entity
    payment_type = sources.custom_order.payment_type if sources.custom_order else None

    supply_at = _supply_at(order)
    supply_local = _local(supply_at) if supply_at is not None else None

    lines = [
        {
            "title": item.product_name,
            "quantity": item.quantity,
            "unit_price": _amount(item.unit_price),
            "net": _amount(item.tax_exclusive_total_price),
            "vat": _amount(item.tax_amount),
            "total": _amount(item.total_price),
        }
        for item in sorted(order.items, key=_sort_key)
    ]
    charges = [
        {
            "title": charge.name,
            "quantity": 1,
            "unit_price": _amount(charge.amount),
            "net": _amount(charge.tax_exclusive_amount),
            "vat": _amount(charge.tax_amount),
            "total": _amount(charge.amount),
        }
        for charge in sorted(order.order_charges, key=_sort_key)
    ]
    taxes = [
        {
            "label": f"{tax.name} {_rate_label(tax.rate)}",
            "taxable": _amount(tax.taxable_amount),
            "amount": _amount(tax.amount),
        }
        for tax in sorted(order.order_taxes, key=lambda t: (t.name, t.rate))
    ]

    discount = money(order.discount_amount)
    rounding = money(order.rounding_amount)
    phone = (order.customer_phone or "").strip()
    if not phone and isinstance(order.shipping_address_snapshot, dict):
        phone = (order.shipping_address_snapshot.get("phone") or "").strip()

    return {
        "currency": _CURRENCY,
        "title": entity.invoice_title or "Tax Invoice",
        "invoice_number": order.order_number,
        "supply_date": supply_local.strftime("%d %b %Y") if supply_local else "—",
        "supply_w3c": supply_local.isoformat(timespec="seconds")
        if supply_local
        else None,
        "entity": {
            "legal_name": entity.legal_name,
            "brand_name": entity.brand_name,
            "registered_address": (entity.registered_address or "").strip(),
            "tax_number": (entity.tax_number or "").strip(),
            "trade_license_number": (entity.trade_license_number or "").strip(),
            "logo_data_uri": sources.logo_data_uri,
        },
        "show_vat": bool(entity.vat_registered),
        "customer": {
            "name": (order.customer_name or "").strip(),
            "email": (order.email or "").strip(),
            "phone": phone,
            "address_lines": _address_lines(order.shipping_address_snapshot),
        },
        "lines": lines + charges,
        "discount": _amount(discount) if discount > 0 else None,
        "total_excl_vat": _amount(order.total_excl_vat),
        "taxes": taxes,
        "vat_amount": _amount(order.vat_amount),
        "rounding": _amount(rounding) if rounding != 0 else None,
        "total": _amount(order.total),
        "payment_method": _PAYMENT_LABELS.get(payment_type or ""),
        "bank_details": _bank_details(entity)
        if payment_type == CustomOrderPaymentTypeEnum.BANK_TRANSFER.value
        else [],
    }


# ─── Rendering ────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _jinja_env():
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["font_uri"] = lambda name: (_FONTS_DIR / name).as_uri()
    return env


def render_invoice_html(sources: InvoiceSources) -> str:
    return (
        _jinja_env()
        .get_template("custom_order_invoice.html")
        .render(inv=build_invoice_context(sources))
    )


def _pdf_identifier(order: Order) -> bytes:
    return order.id.hex.encode("ascii")


def _write_pdf(html: str, identifier: bytes) -> bytes:
    """WeasyPrint layout. Pure CPU; runs in a worker thread."""
    from weasyprint import HTML

    return HTML(string=html, base_url=str(_TEMPLATES_DIR)).write_pdf(
        pdf_identifier=identifier
    )


async def _render(sources: InvoiceSources) -> bytes:
    html = render_invoice_html(sources)
    return await asyncio.to_thread(_write_pdf, html, _pdf_identifier(sources.order))


async def render_invoice_pdf(db: AsyncSession, order: Order) -> bytes:
    """The invoice PDF. Raises `CustomOrderInvoiceUnavailable` when not ready."""
    _require_ready(order)
    return await _render(await _load_sources(db, order))


async def send_invoice(db: AsyncSession, order: Order) -> dict:
    """Email the invoice PDF to the customer, copied to the entity's CCs.

    Returns `send_with_attachment`'s result (`status`, `resend_id`, `error`);
    a failed send is journalled in `email_logs` and returned, not raised.
    """
    _require_ready(order)
    sources = await _load_sources(db, order)
    pdf = await _render(sources)
    loaded, entity = sources.order, sources.entity
    title = entity.invoice_title or "Tax Invoice"
    subject = f"{title} {loaded.order_number} | {entity.brand_name}"
    context = build_invoice_context(sources)
    html = email_service.render_email(
        "custom_order_invoice.html",
        recipient_email=loaded.email,
        locale="en",
        invoice_title=title,
        invoice_number=loaded.order_number,
        customer_name=context["customer"]["name"],
        brand_name=entity.brand_name,
        supply_date=context["supply_date"],
        total=f"{_CURRENCY} {context['total']}",
        pays_by_bank_transfer=bool(context["bank_details"]),
    )
    return await email_service.send_with_attachment(
        loaded.email,
        subject,
        html,
        filename=f"{loaded.order_number}.pdf",
        content=pdf,
        template="custom_order_invoice",
        cc=list(entity.invoice_cc_emails or []) or None,
        order_number=loaded.order_number,
    )
