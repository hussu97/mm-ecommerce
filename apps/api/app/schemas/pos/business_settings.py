"""The shop-wide POS settings row."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ._base import OrderTypeLiteral, ORMModel


class BusinessSettingsUpdate(BaseModel):
    business_name: str | None = Field(None, min_length=1, max_length=200)
    logo_url: str | None = Field(None, max_length=500)
    currency_code: str | None = Field(None, min_length=3, max_length=3)
    currency_symbol: str | None = Field(None, max_length=8)
    decimal_places: int | None = Field(None, ge=0, le=4)
    timezone: str | None = Field(None, max_length=64)

    receipt_logo_url: str | None = Field(None, max_length=500)
    receipt_language_mode: Literal["main", "localized", "both"] | None = None
    receipt_main_language: str | None = Field(None, max_length=5)
    receipt_localized_language: str | None = Field(None, max_length=5)
    receipt_header: str | None = None
    receipt_footer: str | None = None
    invoice_title: str | None = Field(None, max_length=120)
    receipt_show_order_number: bool | None = None
    receipt_show_calories: bool | None = None
    receipt_show_subtotal: bool | None = None
    receipt_show_rounding: bool | None = None
    receipt_show_closer_username: bool | None = None
    receipt_show_creator_username: bool | None = None
    receipt_show_check_number: bool | None = None
    receipt_hide_free_modifiers: bool | None = None
    receipt_show_pickup_phone: bool | None = None
    receipt_show_qr: bool | None = None

    kitchen_sorting: Literal["as_added", "by_category"] | None = None
    kitchen_show_default_modifiers: bool | None = None
    kitchen_auto_print_on_send: bool | None = None

    inventory_logo_url: str | None = Field(None, max_length=500)
    inventory_header: str | None = None
    inventory_footer: str | None = None
    prevent_negative_stock: bool | None = None

    require_customer_for_delivery: bool | None = None
    default_order_type: OrderTypeLiteral | None = None
    cash_rounding_step: Decimal | None = Field(None, ge=0, le=10)
    auto_logout_seconds: int | None = Field(None, ge=0, le=86400)
    order_number_reset_daily: bool | None = None
    enable_tips: bool | None = None
    extra: dict | None = None


class BusinessSettingsResponse(ORMModel):
    id: UUID
    business_name: str
    logo_url: str | None
    currency_code: str
    currency_symbol: str
    decimal_places: int
    timezone: str

    receipt_logo_url: str | None
    receipt_language_mode: str
    receipt_main_language: str
    receipt_localized_language: str
    receipt_header: str | None
    receipt_footer: str | None
    invoice_title: str
    receipt_show_order_number: bool
    receipt_show_calories: bool
    receipt_show_subtotal: bool
    receipt_show_rounding: bool
    receipt_show_closer_username: bool
    receipt_show_creator_username: bool
    receipt_show_check_number: bool
    receipt_hide_free_modifiers: bool
    receipt_show_pickup_phone: bool
    receipt_show_qr: bool

    kitchen_sorting: str
    kitchen_show_default_modifiers: bool
    kitchen_auto_print_on_send: bool

    inventory_logo_url: str | None
    inventory_header: str | None
    inventory_footer: str | None
    prevent_negative_stock: bool

    require_customer_for_delivery: bool
    default_order_type: str
    cash_rounding_step: Decimal
    auto_logout_seconds: int
    order_number_reset_daily: bool
    enable_tips: bool
    extra: dict
    created_at: datetime
    updated_at: datetime
