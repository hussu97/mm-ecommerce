from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, UUIDMixin, status_vocabulary, utcnow


class EmailLogStatusEnum(str, enum.Enum):
    """What `email_service._send` decided happened.

    `skipped` is not a failure: no recipient, a malformed address, or sending
    switched off. It is separated from `failed` so a quiet mailbox can be told
    apart from a broken one.
    """

    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class EmailTemplate(enum.StrEnum):
    """Every email the shop sends: its `email_logs.template` key, the name the
    admin shows for it, and whether a customer receives it.

    The one list. The admin's Email Log filter and Template column are read
    from it (`GET /email-logs/admin/templates`), and so is the set of emails
    whose links carry UTM tags. The admin used to keep its own copy, and it
    stopped at twelve while the shop grew to twenty-one — an inventory report
    showed as a raw key and could not be filtered for at all. `_log` names an
    unregistered key in the error log, and `test_email_template_registry` fails
    the build on one, so a new email has to be added here to ship.

    The key is the template's filename minus `.html` where it has one.
    """

    def __new__(cls, key: str, label: str, customer: bool) -> EmailTemplate:
        member = str.__new__(cls, key)
        member._value_ = key
        member.label = label
        member.customer = customer
        return member

    label: str
    customer: bool

    # ── To the customer ──
    ORDER_CONFIRMATION = ("order_confirmation", "Order Confirmation", True)
    PAYMENT_FAILED = ("payment_failed", "Payment Failed", True)
    ORDER_PACKED = ("order_packed", "Order Packed", True)
    ORDER_OUT_FOR_DELIVERY = ("order_out_for_delivery", "Out for Delivery", True)
    ORDER_DELIVERED = ("order_delivered", "Order Delivered", True)
    ORDER_UNDELIVERED = ("order_undelivered", "Order Undelivered", True)
    ORDER_CANCELLED = ("order_cancelled", "Order Cancelled", True)
    ORDER_REFUNDED = ("order_refunded", "Order Refunded", True)
    #: A checkout started and never paid (an order exists, at `created`).
    ABANDONED_CART = ("abandoned_cart", "Abandoned Checkout", True)
    #: A basket filled and never taken to checkout (no order yet).
    ABANDONED_BASKET = ("abandoned_basket", "Abandoned Basket", True)
    CUSTOM_ORDER_INVOICE = ("custom_order_invoice", "Custom Order Invoice", True)
    WELCOME = ("welcome", "Welcome", True)
    PASSWORD_RESET = ("password_reset", "Password Reset", True)

    # ── To the shop ──
    OWNER_ORDER_NOTIFICATION = (
        "owner_order_notification",
        "Owner Order Notification",
        False,
    )
    CUSTOM_ORDER_ENQUIRY = ("custom_order_enquiry", "Custom Order Enquiry", False)
    DAILY_SALES_REPORT = ("daily_sales_report", "Daily Sales Report", False)
    INVENTORY_REPORT_SUBMITTED = (
        "inventory_report_submitted",
        "Inventory Report Submitted",
        False,
    )
    TRANSFER_SENDING_VARIANCE = (
        "transfer_sending_variance",
        "Transfer Sending Variance",
        False,
    )
    PURCHASE_ORDER_RECEIVING_VARIANCE = (
        "purchase_order_receiving_variance",
        "PO Receiving Variance",
        False,
    )
    COUNTER_PRICING_MISMATCH = (
        "counter_pricing_mismatch",
        "Counter Pricing Mismatch",
        False,
    )
    AUTO_AVAILABILITY_CHANGE = (
        "auto_availability_change",
        "Auto Availability Change",
        False,
    )

    @classmethod
    def label_for(cls, key: str) -> str:
        """The admin's name for *key*: its registered label, or — for a key
        journalled before it was retired or renamed — the key made readable
        (`inventory_report_submitted` -> "Inventory report submitted"), so the
        screen never shows a raw identifier."""
        try:
            return cls(key).label
        except ValueError:
            return key.replace("_", " ").strip().capitalize() or key


class EmailLog(Base, UUIDMixin):
    """Persists every email send attempt for visibility and debugging."""

    __tablename__ = "email_logs"
    # Migration 138. The vocabulary used to live in a trailing comment on the
    # column, which is not a thing the database can check.
    __table_args__ = (status_vocabulary("email_logs", "status", EmailLogStatusEnum),)

    template: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    recipient: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: Copied addresses, when the email had any (a custom-order invoice copies
    #: the owners). Journalled so "did the owner get it" is answerable here.
    cc: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)), nullable=True)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    # 64 since migration 286: a local-first counter order number runs to 40.
    order_number: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    #: What a non-order email is about — an inventory report id, a transfer or
    #: purchase-order reference. Kept out of `order_number`, which the admin
    #: links to `/orders/{n}`.
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    resend_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
