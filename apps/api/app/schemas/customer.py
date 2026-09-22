from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.courier import CourierBadge


class CustomerSummary(BaseModel):
    id: str
    name: str | None
    email: str | None
    phone: str | None
    phone_country: str | None
    order_count: int
    earliest_order_at: str | None
    latest_order_at: str | None
    total_revenue: float
    aov: float


class PaginatedCustomers(BaseModel):
    items: list[CustomerSummary]
    total: int
    page: int
    per_page: int
    pages: int


class CustomerOrderHistoryRow(BaseModel):
    id: str
    order_number: str
    customer_name: str | None
    customer_phone: str | None
    customer_email: str | None
    order_date: str
    order_channel: str
    #: Stable carrier/channel code. Counter and pickup are synthetic display
    #: channels; every other value is a courier or marketplace code.
    order_channel_code: str | None
    courier: CourierBadge | None = None
    order_value: float


class PaginatedCustomerOrders(BaseModel):
    items: list[CustomerOrderHistoryRow]
    total: int
    page: int
    per_page: int
    pages: int


class CustomerDeliveryAreaChannelMetrics(BaseModel):
    customer_count: int
    order_count: int
    revenue: float
    aov: float


class CustomerDeliveryAreaMetrics(CustomerDeliveryAreaChannelMetrics):
    """Aggregate demand, with no individual customer location exposed."""

    channel_breakdown: dict[str, CustomerDeliveryAreaChannelMetrics]


class CustomerDeliveryAreaCell(CustomerDeliveryAreaMetrics):
    """One compact heat-map bucket, never an individual address."""

    latitude: float
    longitude: float


class CustomerDeliveryAreaZone(CustomerDeliveryAreaMetrics):
    id: str
    name: str
    fulfilment_provider: str
    geometry: dict[str, Any]


class CustomerDeliveryAreas(BaseModel):
    """Live zones plus filtered, privacy-preserving delivery-demand cells."""

    version_name: str | None
    zones: list[CustomerDeliveryAreaZone]
    cells: list[CustomerDeliveryAreaCell]
    customer_count: int
    order_count: int
    revenue: float
    aov: float
    source_counts: dict[str, int]
