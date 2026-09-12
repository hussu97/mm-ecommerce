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
            pickup_contact={
                "first_name": "Test",
                "last_name": "Customer",
                "phone": "+971501234567",
            },
        )
        assert order.delivery_method == DeliveryMethodEnum.PICKUP
        assert order.pickup_contact is not None

    def test_pickup_requires_a_contact(self):
        # A pickup order has no address to carry a name and number, so it must
        # bring its own — refused with a message the customer can act on.
        with pytest.raises(ValidationError):
            OrderCreate(
                email="test@example.com",
                delivery_method=DeliveryMethodEnum.PICKUP,
                payment_method="stripe",
            )

    def test_receiver_is_dropped_on_a_pickup_order(self):
        # A gift recipient is a delivery-only idea; it is dropped rather than
        # rejected so an older client that always sends it cannot wedge.
        order = OrderCreate(
            email="test@example.com",
            delivery_method=DeliveryMethodEnum.PICKUP,
            payment_method="stripe",
            pickup_contact={
                "first_name": "Test",
                "last_name": "Customer",
                "phone": "+971501234567",
            },
            receiver={"name": "Someone Else", "phone": "+971502223333"},
        )
        assert order.receiver is None

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

    def test_email_is_required(self):
        """
        Email used to be optional, on the reasoning that a phone number is
        enough to run a delivery. It is required now: every order gets written
        confirmation, and an order with no address was confirmed to nobody —
        the mailer refuses the `…@guest.local` placeholder that stood in for it.
        """
        with pytest.raises(ValidationError):
            OrderCreate(
                delivery_method=DeliveryMethodEnum.PICKUP, payment_method="stripe"
            )

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"email": None},
            {"email": ""},
            {"email": "   "},
        ],
        ids=["absent", "null", "empty", "whitespace"],
    )
    def test_a_missing_email_says_what_to_do(self, payload):
        """
        The four shapes a browser holding yesterday's bundle can post, and one
        sentence for all of them.

        Pydantic's own words here are "Field required", "Input should be a valid
        string" and "value is not a valid email address" depending on which of
        these arrives — and the storefront's client joins those `msg` strings
        straight into the toast the customer reads. None of them names the field
        or says what to type.
        """
        with pytest.raises(ValidationError) as exc:
            OrderCreate(
                delivery_method=DeliveryMethodEnum.PICKUP,
                payment_method="stripe",
                **payload,
            )

        errors = exc.value.errors()
        assert len(errors) == 1
        assert errors[0]["msg"] == (
            "Please enter your email address — your order confirmation is sent to it."
        )
        # No "Value error, " in front of it, which a plain `ValueError` would
        # have added — see `_email_is_not_optional`.
        assert not errors[0]["msg"].startswith("Value error")

    def test_a_typo_still_gets_pydantic_s_own_words(self):
        """
        The custom message covers *absent*, not *wrong*. "Not a valid email
        address" is already the right thing to tell somebody who typed one.
        """
        with pytest.raises(ValidationError) as exc:
            OrderCreate(
                email="not-email",
                delivery_method=DeliveryMethodEnum.PICKUP,
                payment_method="stripe",
            )
        assert exc.value.errors()[0]["loc"] == ("email",)
        assert "valid email address" in exc.value.errors()[0]["msg"]


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
