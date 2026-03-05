from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.order import DeliveryMethodEnum, OrderStatusEnum
from app.schemas.order import OrderCreate, OrderStatusUpdate


class TestOrderCreate:
    def test_valid_delivery_order(self):
        order = OrderCreate(
            email="test@example.com",
            delivery_method=DeliveryMethodEnum.DELIVERY,
            payment_method="stripe",
        )
        assert order.delivery_method == DeliveryMethodEnum.DELIVERY

    def test_valid_pickup_order(self):
        order = OrderCreate(
            email="test@example.com",
            delivery_method=DeliveryMethodEnum.PICKUP,
            payment_method="stripe",
        )
        assert order.delivery_method == DeliveryMethodEnum.PICKUP

    def test_invalid_delivery_method(self):
        with pytest.raises(ValidationError):
            OrderCreate(
                email="test@example.com",
                delivery_method="invalid",
                payment_method="stripe",
            )

    def test_invalid_email(self):
        with pytest.raises(ValidationError):
            OrderCreate(
                email="not-email",
                delivery_method=DeliveryMethodEnum.PICKUP,
                payment_method="stripe",
            )

    def test_email_required(self):
        with pytest.raises(ValidationError):
            OrderCreate(
                delivery_method=DeliveryMethodEnum.PICKUP, payment_method="stripe"
            )


class TestOrderStatusUpdate:
    def test_valid_status(self):
        update = OrderStatusUpdate(status=OrderStatusEnum.CONFIRMED)
        assert update.status == OrderStatusEnum.CONFIRMED

    def test_invalid_status(self):
        with pytest.raises(ValidationError):
            OrderStatusUpdate(status="invalid_status")

    def test_all_status_values_valid(self):
        for status in OrderStatusEnum:
            update = OrderStatusUpdate(status=status)
            assert update.status == status
