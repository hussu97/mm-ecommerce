from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Boolean, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class ReceiptLanguageModeEnum(str, enum.Enum):
    MAIN = "main"
    LOCALIZED = "localized"
    BOTH = "both"


class KitchenSortingEnum(str, enum.Enum):
    AS_ADDED = "as_added"
    BY_CATEGORY = "by_category"


class BusinessSettings(Base, UUIDMixin, TimestampMixin):
    """
    Singleton row holding business-wide POS configuration. Branches may override
    the receipt header/footer; everything else is global.
    """

    __tablename__ = "business_settings"

    # ─── Identity ─────────────────────────────────────────────────────────────
    business_name: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default="Melting Moments"
    )
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    currency_code: Mapped[str] = mapped_column(
        String(3), nullable=False, server_default="AED"
    )
    currency_symbol: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default="AED"
    )
    decimal_places: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="2"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default="Asia/Dubai"
    )

    # ─── Receipt ──────────────────────────────────────────────────────────────
    receipt_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    receipt_language_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=ReceiptLanguageModeEnum.MAIN.value
    )
    receipt_main_language: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="en"
    )
    receipt_localized_language: Mapped[str] = mapped_column(
        String(5), nullable=False, server_default="ar"
    )
    receipt_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    receipt_footer: Mapped[str | None] = mapped_column(Text, nullable=True)
    invoice_title: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default="Tax Invoice"
    )
    receipt_show_order_number: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    receipt_show_calories: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    receipt_show_subtotal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    receipt_show_rounding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    receipt_show_closer_username: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    receipt_show_creator_username: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    receipt_show_check_number: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    receipt_hide_free_modifiers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    receipt_show_pickup_phone: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    receipt_show_qr: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    # ─── Kitchen ──────────────────────────────────────────────────────────────
    kitchen_sorting: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=KitchenSortingEnum.AS_ADDED.value
    )
    kitchen_show_default_modifiers: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    kitchen_auto_print_on_send: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    # ─── Custom cakes ─────────────────────────────────────────────────────────
    #: How many custom cakes may be booked for one date.
    #:
    #: One, because that is what this kitchen can actually make alongside a
    #: day's normal production. Configurable because the honest answer changes
    #: with staff, and hard-coding it would mean a deploy to hire someone.
    custom_orders_per_day: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    #: Days of notice a custom order needs when its product does not say.
    custom_order_lead_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="3"
    )
    #: How far ahead the storefront will let someone book. Stops a booking for
    #: a date nobody can plan staffing for.
    custom_order_max_days_ahead: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="180"
    )

    # ─── Inventory transactions ───────────────────────────────────────────────
    inventory_logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    inventory_header: Mapped[str | None] = mapped_column(Text, nullable=True)
    inventory_footer: Mapped[str | None] = mapped_column(Text, nullable=True)
    prevent_negative_stock: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # ─── Cashier app behaviour ────────────────────────────────────────────────
    require_customer_for_delivery: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    default_order_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pickup"
    )
    # Cash totals are rounded to this step (0 disables rounding).
    cash_rounding_step: Mapped[Any] = mapped_column(
        Numeric(6, 3), nullable=False, server_default="0"
    )
    auto_logout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    order_number_reset_daily: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    enable_tips: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )

    # Escape hatch for settings that don't warrant a column yet.
    extra: Mapped[Any] = mapped_column(JSONB, nullable=False, server_default="{}")

    def __repr__(self) -> str:
        return f"<BusinessSettings {self.business_name}>"
