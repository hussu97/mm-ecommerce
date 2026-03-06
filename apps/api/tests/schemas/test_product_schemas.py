from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.product import ProductCreate


class TestProductCreate:
    def test_valid_product(self):
        p = ProductCreate(
            name="Test Product", slug="test-product", base_price=Decimal("10.00")
        )
        assert p.name == "Test Product"

    def test_negative_price_invalid(self):
        with pytest.raises(ValidationError):
            ProductCreate(name="Test", slug="test", base_price=Decimal("-5.00"))

    def test_name_required(self):
        with pytest.raises(ValidationError):
            ProductCreate(slug="test", base_price=Decimal("10.00"))

    def test_slug_pattern_valid(self):
        p = ProductCreate(
            name="Test", slug="valid-slug-123", base_price=Decimal("10.00")
        )
        assert p.slug == "valid-slug-123"

    def test_slug_with_uppercase_invalid(self):
        with pytest.raises(ValidationError):
            ProductCreate(name="Test", slug="Invalid-Slug", base_price=Decimal("10.00"))

    def test_slug_with_spaces_invalid(self):
        with pytest.raises(ValidationError):
            ProductCreate(name="Test", slug="my slug", base_price=Decimal("10.00"))
