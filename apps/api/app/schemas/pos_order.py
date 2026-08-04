"""Schemas for the POS order engine."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

OrderTypeLiteral = Literal["dine_in", "pickup", "delivery", "drive_thru"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SelectedOption(BaseModel):
    modifier_option_id: UUID | None = None
    name: str
    sku: str | None = None
    price: Decimal = Decimal("0")
    quantity: int = 1


class OpenOrderRequest(BaseModel):
    branch_id: UUID
    order_type: OrderTypeLiteral
    till_id: UUID | None = None
    device_id: UUID | None = None
    table_id: UUID | None = None
    guests: int = Field(1, ge=1, le=100)
    customer_id: UUID | None = None
    customer_name: str | None = Field(None, max_length=150)
    customer_phone: str | None = Field(None, max_length=30)
    notes: str | None = None
    source: Literal["cashier", "online", "api", "call_center"] = "cashier"
    #: Ahead orders — a cake wanted at 4pm tomorrow.
    due_at: datetime | None = None
    #: Which delivery zone the address falls in.


class AddItemRequest(BaseModel):
    product_id: UUID
    quantity: int = Field(1, ge=1, le=999)
    #: Only honoured for products whose pricing_method is "open".
    unit_price: Decimal | None = Field(None, ge=0)
    selected_options: list[SelectedOption] = Field(default_factory=list)
    kitchen_notes: str | None = None
    #: Fire this line with a named course rather than immediately.
    course_id: UUID | None = None
    #: Required for a product priced per kilo; rejected for anything else.
    weight: Decimal | None = Field(None, gt=0, decimal_places=3)


class SplitOrderRequest(BaseModel):
    #: The lines to move onto the new check.
    item_ids: list[UUID] = Field(min_length=1)


class SplitOrderResponse(BaseModel):
    original: "PosOrderResponse"
    split: "PosOrderResponse"


class JoinOrderRequest(BaseModel):
    #: The check being absorbed; the one in the path survives.
    source_order_id: UUID


class ChangeTableRequest(BaseModel):
    #: Null lifts the check off the floor plan without closing it.
    table_id: UUID | None = None


class AssignDriverRequest(BaseModel):
    #: Null takes the order back off the driver, e.g. a shift change.
    driver_id: UUID | None = None


class ScheduleOrderRequest(BaseModel):
    #: When the customer wants it; null clears the schedule.
    due_at: datetime | None = None


class VoidItemRequest(BaseModel):
    reason_id: UUID | None = None


class ReturnItemRequest(BaseModel):
    quantity: int = Field(ge=1, le=999)
    reason_id: UUID | None = None


class ApplyDiscountRequest(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    is_percentage: bool = False
    value: Decimal = Field(gt=0)
    source: Literal["open", "predefined", "coupon", "loyalty", "promotion"] = "open"
    order_item_id: UUID | None = None
    reference_id: UUID | None = None


class ApplyChargeRequest(BaseModel):
    charge_id: UUID | None = None
    name: str | None = Field(None, max_length=100)
    type: Literal["percentage", "fixed", "open"] = "fixed"
    value: Decimal = Field(Decimal("0"), ge=0)


class PaymentRequest(BaseModel):
    payment_method_id: UUID
    amount: Decimal = Field(gt=0)
    tendered: Decimal | None = Field(None, ge=0)
    tips: Decimal = Field(Decimal("0"), ge=0)
    till_id: UUID | None = None
    is_refund: bool = False
    reference: str | None = Field(None, max_length=120)


class VoidOrderRequest(BaseModel):
    reason_id: UUID | None = None


class OrderItemResponse(ORMModel):
    id: UUID
    product_id: UUID | None
    product_name: str
    product_sku: str
    quantity: int
    returned_quantity: int
    weight: Decimal | None = None
    base_price: Decimal
    options_price: Decimal
    unit_price: Decimal
    total_price: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    tax_exclusive_total_price: Decimal
    selected_options_snapshot: list = []
    status: str | None
    is_open_price: bool
    kitchen_notes: str | None
    kitchen_flow_id: UUID | None
    course_id: UUID | None = None
    sent_to_kitchen_at: datetime | None
    added_at: datetime | None


class OrderPaymentResponse(ORMModel):
    id: UUID
    payment_method_id: UUID
    amount: Decimal
    tendered: Decimal
    tips: Decimal
    change_given: Decimal
    is_refund: bool
    reference: str | None
    recorded_at: datetime


class OrderChargeResponse(ORMModel):
    id: UUID
    charge_id: UUID | None
    name: str
    type: str
    value: Decimal
    amount: Decimal


class OrderDiscountResponse(ORMModel):
    id: UUID
    order_item_id: UUID | None
    source: str
    name: str
    is_percentage: bool
    value: Decimal
    amount: Decimal


class OrderTaxResponse(ORMModel):
    id: UUID
    tax_id: UUID | None
    name: str
    rate: Decimal
    taxable_amount: Decimal
    amount: Decimal


class PosOrderResponse(ORMModel):
    id: UUID
    order_number: str
    check_number: int | None
    #: Set on the child of a split, and on a check that was joined into
    #: another, so a terminal can show where a check came from or went.
    original_order_id: UUID | None = None
    branch_id: UUID | None
    table_id: UUID | None
    device_id: UUID | None
    till_id: UUID | None
    order_type: str | None
    source: str | None
    pos_status: str | None
    delivery_status: str | None
    business_date: str | None
    guests: int
    customer_id: UUID | None = None
    customer_name: str | None
    customer_phone: str | None
    notes: str | None
    kitchen_notes: str | None
    #: Where a website order is going, flattened for the ticket. Null for a
    #: counter order, which has nowhere to go but the counter.
    delivery_address: str | None = None

    subtotal: Decimal
    discount_amount: Decimal
    charges_amount: Decimal
    vat_amount: Decimal
    total_excl_vat: Decimal
    rounding_amount: Decimal
    tips_amount: Decimal
    total: Decimal
    amount_paid: Decimal = Decimal("0")
    balance_due: Decimal = Decimal("0")

    creator_id: UUID | None
    closer_id: UUID | None
    opened_at: datetime | None
    closed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    items: list[OrderItemResponse] = []
    payments: list[OrderPaymentResponse] = []
    order_charges: list[OrderChargeResponse] = []
    order_discounts: list[OrderDiscountResponse] = []
    order_taxes: list[OrderTaxResponse] = []


class KitchenTicketItemResponse(ORMModel):
    id: UUID
    order_item_id: UUID | None
    product_name: str
    quantity: int
    modifiers_summary: str | None
    notes: str | None
    status: str
    completed_at: datetime | None


class KitchenTicketResponse(ORMModel):
    id: UUID
    order_id: UUID
    kitchen_flow_id: UUID | None
    branch_id: UUID
    sequence: int
    status: str
    sent_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    printed_at: datetime | None
    reprint_count: int
    items: list[KitchenTicketItemResponse] = []
    # Denormalised for the KDS header, which shows "Table 4 · Order 12".
    order_number: str | None = None
    check_number: int | None = None
    order_type: str | None = None
    table_name: str | None = None


class KitchenTicketStatusUpdate(BaseModel):
    status: Literal["new", "in_progress", "ready", "completed", "cancelled"]
