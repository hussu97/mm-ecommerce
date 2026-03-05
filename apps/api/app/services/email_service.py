from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import resend
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import settings
from app.schemas.order import OrderResponse

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


def _send(to: str, subject: str, html: str) -> None:
    """Send an email via Resend. Logs errors without raising so the main flow is unaffected."""
    if not settings.RESEND_API_KEY:
        logger.warning("RESEND_API_KEY not set — skipping email to %s: %s", to, subject)
        return

    try:
        resend.api_key = settings.RESEND_API_KEY
        params: resend.Emails.SendParams = {
            "from": f"Melting Moments <{settings.FROM_EMAIL}>",
            "to": to,
            "subject": subject,
            "html": html,
        }
        response = resend.Emails.send(params)
        logger.info("Email sent: id=%s to=%s subject=%s", response.get("id"), to, subject)
    except Exception as exc:
        logger.error("Email send failed to=%s subject=%s", to, subject, exc_info=True)


# ─── Order Emails ─────────────────────────────────────────────────────────────

def send_order_confirmation(order: OrderResponse) -> None:
    name = order.shipping_address_snapshot.get("first_name", "there") if order.shipping_address_snapshot else "there"
    html = _render(
        "order_confirmation.html",
        recipient_email=order.email,
        name=name,
        order=order,
    )
    _send(order.email, f"Order Confirmed — {order.order_number} | Melting Moments", html)


def send_order_packed(order: OrderResponse) -> None:
    name = order.shipping_address_snapshot.get("first_name", "there") if order.shipping_address_snapshot else "there"
    if order.delivery_method.value == "delivery":
        subject = f"Your Order is On Its Way — {order.order_number}"
    else:
        subject = f"Ready for Pickup — {order.order_number}"
    html = _render(
        "order_packed.html",
        recipient_email=order.email,
        name=name,
        order=order,
    )
    _send(order.email, subject, html)


def send_order_cancelled(order: OrderResponse) -> None:
    name = order.shipping_address_snapshot.get("first_name", "there") if order.shipping_address_snapshot else "there"
    html = _render(
        "order_cancelled.html",
        recipient_email=order.email,
        name=name,
        order=order,
    )
    _send(order.email, f"Order Cancelled — {order.order_number} | Melting Moments", html)


# ─── User Emails ──────────────────────────────────────────────────────────────

def send_welcome(email: str, first_name: str) -> None:
    html = _render(
        "welcome.html",
        recipient_email=email,
        first_name=first_name,
    )
    _send(email, "Welcome to Melting Moments!", html)


def send_password_reset(email: str, first_name: str, reset_token: str) -> None:
    reset_link = f"{settings.WEB_URL}/reset-password?token={reset_token}"
    html = _render(
        "password_reset.html",
        recipient_email=email,
        first_name=first_name,
        reset_link=reset_link,
    )
    _send(email, "Reset Your Password — Melting Moments", html)
