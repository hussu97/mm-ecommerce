"""Custom orders — bespoke cakes taken by the shop (admin console and the POS).

A custom order is an `orders` row with `source = 'custom'`; these are the shapes
of the screens that take, make and finish one. Money is VAT-inclusive, as on
every other channel, and computed by the API — a client renders what it gets.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, model_validator

PaymentTypeLiteral = Literal["bank_transfer", "card", "cash"]
CardFeeModeLiteral = Literal["separate_line", "included"]


# ─── Requests ──────────────────────────────────────────────────────────────────


class CustomOrderLineIn(BaseModel):
    #: What the kitchen and the invoice call this line ("3-tier red velvet").
    title: str = Field(min_length=1, max_length=200)
    quantity: int = Field(1, ge=1, le=999)
    #: VAT-inclusive, per unit.
    unit_price: Decimal = Field(ge=0, le=Decimal("100000"), decimal_places=2)
    notes: str | None = Field(None, max_length=1000)


class CustomOrderRecipeLineIn(BaseModel):
    item_id: UUID
    #: In the item's ingredient unit (grams for a ganache).
    quantity: Decimal = Field(gt=0, le=Decimal("1000000"))


class CustomOrderCustomerIn(BaseModel):
    """All optional, in any combination — a DM may come with only a name."""

    name: str | None = Field(None, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=30)


class CustomOrderAddressIn(BaseModel):
    """All optional, in any combination; a pin needs both coordinates."""

    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    address_line_1: str | None = Field(None, max_length=255)
    unit_number: str | None = Field(None, max_length=50)

    @model_validator(mode="after")
    def _pin_is_whole(self) -> CustomOrderAddressIn:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("A location pin needs both latitude and longitude")
        return self


class _PaymentFields(BaseModel):
    payment_type: PaymentTypeLiteral | None = None
    #: Required with a card payment and refused without one.
    card_fee_mode: CardFeeModeLiteral | None = None

    @model_validator(mode="after")
    def _fee_mode_iff_card(self):
        if self.payment_type == "card" and self.card_fee_mode is None:
            raise ValueError(
                "A card payment needs a fee choice: a separate line or included"
            )
        if self.payment_type != "card" and self.card_fee_mode is not None:
            raise ValueError("Only a card payment has a card fee")
        return self


class CustomOrderCreate(_PaymentFields):
    lines: list[CustomOrderLineIn] = Field(min_length=1, max_length=50)
    delivery_date: date
    #: Optional; a date alone is a promise for the day.
    delivery_time: time | None = None
    customer: CustomOrderCustomerIn = Field(default_factory=CustomOrderCustomerIn)
    address: CustomOrderAddressIn | None = None
    recipe: list[CustomOrderRecipeLineIn] = Field(default_factory=list, max_length=50)
    notes: str | None = Field(None, max_length=2000)
    enquiry_id: UUID | None = None
    #: A retried create with the same key returns the order it made.
    client_request_id: UUID | None = None


class CustomOrderUpdate(_PaymentFields):
    """Replaces the lines, date, payment and notes. Refused once delivered."""

    lines: list[CustomOrderLineIn] = Field(min_length=1, max_length=50)
    delivery_date: date
    delivery_time: time | None = None
    notes: str | None = Field(None, max_length=2000)


class CustomOrderContactUpdate(BaseModel):
    """Refused once a courier has been booked."""

    customer: CustomOrderCustomerIn = Field(default_factory=CustomOrderCustomerIn)
    address: CustomOrderAddressIn | None = None


class CustomOrderRecipeUpdate(BaseModel):
    """Replaces the recipe. Refused once packed (the recipe has been consumed)."""

    recipe: list[CustomOrderRecipeLineIn] = Field(max_length=50)


class CustomOrderDeliveryChoice(BaseModel):
    """How an admin sends a packed order.

    `slider_car` / `lalamove` book that courier (a Lalamove booking carries
    the quotation the admin was shown); `third_party` records a courier we did
    not book and marks the order delivered.
    """

    mode: Literal["slider_car", "lalamove", "third_party"]
    #: Required for `third_party` (0 allowed): what that courier cost us, VAT
    #: inclusive.
    courier_fee: Decimal | None = Field(None, ge=0, le=Decimal("10000"))
    quotation_id: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def _fee_for_third_party(self) -> CustomOrderDeliveryChoice:
        if self.mode == "third_party" and self.courier_fee is None:
            raise ValueError("A third-party delivery needs its courier fee (0 is fine)")
        return self


class CustomCakeProductionLineIn(BaseModel):
    item_id: UUID
    #: In the item's own unit, as every production order is.
    quantity: Decimal = Field(gt=0, le=Decimal("100000"))


class CustomCakeProductionCreate(BaseModel):
    items: list[CustomCakeProductionLineIn] = Field(min_length=1, max_length=50)
    notes: str | None = Field(None, max_length=1000)
    client_request_id: str | None = Field(None, max_length=64)


# ─── Responses ─────────────────────────────────────────────────────────────────


class CustomOrderLineOut(BaseModel):
    id: UUID
    title: str
    quantity: int
    unit_price: Decimal
    total: Decimal
    notes: str | None


class CustomOrderChargeOut(BaseModel):
    name: str
    amount: Decimal


class CustomOrderRecipeLineOut(BaseModel):
    item_id: UUID
    sku: str | None
    name: str
    #: The ingredient unit `quantity` and `on_hand` are in.
    unit: str | None
    quantity: Decimal
    on_hand: Decimal


class CustomOrderAddressOut(BaseModel):
    latitude: float | None
    longitude: float | None
    address_line_1: str | None
    unit_number: str | None


class CustomOrderDeliveryOut(BaseModel):
    provider: str
    courier_status: str | None
    #: What the courier cost us (VAT inclusive), once known.
    cost: Decimal | None
    share_link: str | None
    driver_name: str | None
    driver_phone: str | None
    last_error: str | None


class CustomOrderActions(BaseModel):
    """What may be done to the order now — decided by the API so the console
    and both POS apps offer the same buttons."""

    can_edit_lines: bool
    can_edit_recipe: bool
    can_edit_contact: bool
    can_pack: bool
    can_collect: bool
    can_choose_delivery: bool
    can_cancel: bool
    #: Why a courier cannot be chosen yet (no pin, name or phone), when not.
    delivery_unavailable_reason: str | None
    #: Why the invoice cannot be produced yet, when not.
    invoice_unavailable_reason: str | None


class CustomOrderResponse(BaseModel):
    id: UUID
    order_number: str
    status: str
    created_via: str
    created_at: datetime
    delivery_date: date | None
    delivery_time: time | None
    delivered_at: datetime | None
    kitchen_printed_at: datetime | None
    customer_name: str | None
    customer_email: str | None
    customer_phone: str | None
    address: CustomOrderAddressOut | None
    payment_type: str | None
    card_fee_mode: str | None
    notes: str | None
    enquiry_id: UUID | None
    lines: list[CustomOrderLineOut]
    charges: list[CustomOrderChargeOut]
    recipe: list[CustomOrderRecipeLineOut]
    subtotal: Decimal
    charges_amount: Decimal
    vat_amount: Decimal
    total_excl_vat: Decimal
    total: Decimal
    #: What taking the card cost us (VAT inclusive); zero off card.
    payment_fee: Decimal | None
    delivery: CustomOrderDeliveryOut | None
    actions: CustomOrderActions


class CustomOrderListItem(BaseModel):
    id: UUID
    order_number: str
    status: str
    delivery_date: date | None
    delivery_time: time | None
    customer_name: str | None
    #: The first line's title — what the order is, at a glance.
    summary: str
    total: Decimal
    kitchen_printed_at: datetime | None
    delivery_provider: str | None


class PaginatedCustomOrders(BaseModel):
    items: list[CustomOrderListItem]
    total: int
    page: int
    per_page: int
    pages: int


class CustomCakeItem(BaseModel):
    """An item a custom order's recipe may use, with the kitchen's stock."""

    id: UUID
    sku: str | None
    name: str
    #: The ingredient unit a recipe quantity is written in.
    unit: str | None
    on_hand: Decimal
    #: The unit production is raised in, and the on-hand in it.
    production_unit: str | None
    on_hand_in_production_unit: Decimal


class CustomOrdersStatus(BaseModel):
    """Whether the channel is configured, and for which branch."""

    enabled: bool
    branch_id: UUID | None
    branch_name: str | None


class CustomOrderClaimResult(BaseModel):
    """Whether this register won the docket; only the winner prints."""

    claimed: bool
    order: CustomOrderResponse


class CustomOrderDeliveryQuote(BaseModel):
    provider: Literal["slider_car", "lalamove"]
    available: bool
    #: VAT-inclusive fare, when available.
    fare: Decimal | None
    #: Why the courier cannot take it, when not available.
    reason: str | None
    #: Lalamove's quotation, to book at the price shown; null for Slider.
    quotation_id: str | None
    expires_at: datetime | None


class CustomOrderDeliveryQuotes(BaseModel):
    quotes: list[CustomOrderDeliveryQuote]
    #: Why no courier can be quoted at all (no pin, name or phone).
    unavailable_reason: str | None
