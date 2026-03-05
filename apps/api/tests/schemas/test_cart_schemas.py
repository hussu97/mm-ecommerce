from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.cart import CartItemCreate, CartItemUpdate


class TestCartItemCreate:
    def test_default_quantity_is_one(self):
        item = CartItemCreate(variant_id=uuid4())
        assert item.quantity == 1

    def test_quantity_min_valid(self):
        item = CartItemCreate(variant_id=uuid4(), quantity=1)
        assert item.quantity == 1

    def test_quantity_max_valid(self):
        item = CartItemCreate(variant_id=uuid4(), quantity=99)
        assert item.quantity == 99

    def test_quantity_zero_invalid(self):
        with pytest.raises(ValidationError):
            CartItemCreate(variant_id=uuid4(), quantity=0)

    def test_quantity_over_max_invalid(self):
        with pytest.raises(ValidationError):
            CartItemCreate(variant_id=uuid4(), quantity=100)

    def test_variant_id_required(self):
        with pytest.raises(ValidationError):
            CartItemCreate()


class TestCartItemUpdate:
    def test_valid_quantity(self):
        update = CartItemUpdate(quantity=5)
        assert update.quantity == 5

    def test_quantity_zero_invalid(self):
        with pytest.raises(ValidationError):
            CartItemUpdate(quantity=0)

    def test_quantity_over_max_invalid(self):
        with pytest.raises(ValidationError):
            CartItemUpdate(quantity=100)

    def test_quantity_required(self):
        with pytest.raises(ValidationError):
            CartItemUpdate()
