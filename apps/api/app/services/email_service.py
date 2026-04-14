from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.core.database import AsyncSessionFactory
from app.models.email_log import EmailLog
from app.schemas.order import OrderResponse

__all__ = [
    "send_order_cancelled",
    "send_order_confirmation",
    "send_order_packed",
    "send_password_reset",
    "send_payment_failed",
    "send_refund_notification",
    "send_welcome",
]

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "emails"

_jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)


def _render(template_name: str, recipient_email: str, **context) -> str:
    template = _jinja_env.get_template(template_name)
    return template.render(
        web_url=settings.WEB_URL,
        now=datetime.now(timezone.utc),
        recipient_email=recipient_email,
        **context,
    )


def _send(to: str, subject: str, html: str) -> dict:
    """
    Send an email via Resend. Never raises — always returns a result dict:
      {"status": "sent"|"failed"|"skipped", "resend_id": str|None, "error": str|None}
    """
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email to %s: %s", to, subject)
        return {
            "status": "skipped",
            "resend_id": None,
            "error": "RESEND_API_KEY not configured",
        }

    try:
        resend.api_key = settings.RESEND_API_KEY
        params: resend.Emails.SendParams = {
            "from": f"Melting Moments <{settings.FROM_EMAIL}>",
            "to": to,
            "subject": subject,
            "html": html,
        }
        response = resend.Emails.send(params)
        resend_id = response.id if hasattr(response, "id") else response.get("id")
        logger.info("Email sent: id=%s to=%s subject=%s", resend_id, to, subject)
        return {"status": "sent", "resend_id": resend_id, "error": None}
    except Exception as exc:
        error_msg = str(exc)
        logger.error(
            "Email send failed to=%s subject=%s: %s",
            to,
            subject,
            error_msg,
            exc_info=True,
        )
        return {"status": "failed", "resend_id": None, "error": error_msg}


async def _log(
    template: str,
    recipient: str,
    subject: str,
    result: dict,
    order_number: str | None = None,
) -> None:
    """Persist an EmailLog row. Swallows all errors so logging never breaks email flow."""
    try:
        async with AsyncSessionFactory() as db:
            db.add(
                EmailLog(
                    template=template,
                    recipient=recipient,
                    subject=subject,
                    order_number=order_number,
                    status=result["status"],
                    resend_id=result.get("resend_id"),
                    error=result.get("error"),
                )
            )
            await db.commit()
    except Exception as exc:
        logger.error("EmailLog persist failed: %s", exc)


# ─── Order Emails ─────────────────────────────────────────────────────────────


async def send_order_confirmation(order: OrderResponse) -> None:
    subject = f"Order Confirmed — {order.order_number} | Melting Moments"
    try:
        name = (
            order.shipping_address_snapshot.get("first_name", "there")
            if order.shipping_address_snapshot
            else "there"
        )
        html = _render(
            "order_confirmation.html",
            recipient_email=order.email,
            name=name,
            order=order,
        )
        result = await asyncio.to_thread(_send, order.email, subject, html)
    except Exception as exc:
        logger.error(
            "order_confirmation render/send failed for %s: %s",
            order.order_number,
            exc,
            exc_info=True,
        )
        result = {"status": "failed", "resend_id": None, "error": str(exc)}
    await _log("order_confirmation", order.email, subject, result, order.order_number)


async def send_order_packed(order: OrderResponse) -> None:
    is_delivery = order.delivery_method.value == "delivery"
    subject = (
        f"Your Order is On Its Way — {order.order_number}"
        if is_delivery
        else f"Ready for Pickup — {order.order_number}"
    )
    try:
        name = (
            order.shipping_address_snapshot.get("first_name", "there")
            if order.shipping_address_snapshot
            else "there"
        )
        html = _render(
            "order_packed.html", recipient_email=order.email, name=name, order=order
        )
        result = await asyncio.to_thread(_send, order.email, subject, html)
    except Exception as exc:
        logger.error(
            "order_packed render/send failed for %s: %s",
            order.order_number,
            exc,
            exc_info=True,
        )
        result = {"status": "failed", "resend_id": None, "error": str(exc)}
    await _log("order_packed", order.email, subject, result, order.order_number)


async def send_payment_failed(order: OrderResponse) -> None:
    subject = f"Payment Failed — {order.order_number} | Melting Moments"
    try:
        name = (
            order.shipping_address_snapshot.get("first_name", "there")
            if order.shipping_address_snapshot
            else "there"
        )
        html = _render(
            "payment_failed.html", recipient_email=order.email, name=name, order=order
        )
        result = await asyncio.to_thread(_send, order.email, subject, html)
    except Exception as exc:
        logger.error(
            "payment_failed render/send failed for %s: %s",
            order.order_number,
            exc,
            exc_info=True,
        )
        result = {"status": "failed", "resend_id": None, "error": str(exc)}
    await _log("payment_failed", order.email, subject, result, order.order_number)


async def send_refund_notification(order: OrderResponse) -> None:
    subject = f"Refund Processed — {order.order_number} | Melting Moments"
    try:
        name = (
            order.shipping_address_snapshot.get("first_name", "there")
            if order.shipping_address_snapshot
            else "there"
        )
        html = _render(
            "order_refunded.html", recipient_email=order.email, name=name, order=order
        )
        result = await asyncio.to_thread(_send, order.email, subject, html)
    except Exception as exc:
        logger.error(
            "order_refunded render/send failed for %s: %s",
            order.order_number,
            exc,
            exc_info=True,
        )
        result = {"status": "failed", "resend_id": None, "error": str(exc)}
    await _log("order_refunded", order.email, subject, result, order.order_number)


async def send_order_cancelled(order: OrderResponse) -> None:
    subject = f"Order Cancelled — {order.order_number} | Melting Moments"
    try:
        name = (
            order.shipping_address_snapshot.get("first_name", "there")
            if order.shipping_address_snapshot
            else "there"
        )
        html = _render(
            "order_cancelled.html", recipient_email=order.email, name=name, order=order
        )
        result = await asyncio.to_thread(_send, order.email, subject, html)
    except Exception as exc:
        logger.error(
            "order_cancelled render/send failed for %s: %s",
            order.order_number,
            exc,
            exc_info=True,
        )
        result = {"status": "failed", "resend_id": None, "error": str(exc)}
    await _log("order_cancelled", order.email, subject, result, order.order_number)


# ─── User Emails ──────────────────────────────────────────────────────────────


async def send_welcome(email: str) -> None:
    subject = "Welcome to Melting Moments!"
    html = _render("welcome.html", recipient_email=email)
    result = await asyncio.to_thread(_send, email, subject, html)
    await _log("welcome", email, subject, result)


async def send_password_reset(email: str, reset_token: str) -> None:
    reset_link = f"{settings.WEB_URL}/reset-password?token={reset_token}"
    subject = "Reset Your Password — Melting Moments"
    html = _render(
        "password_reset.html",
        recipient_email=email,
        reset_link=reset_link,
    )
    result = await asyncio.to_thread(_send, email, subject, html)
    await _log("password_reset", email, subject, result)
