from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.category import CategoryCreate


class TestCategoryCreate:
    def test_valid_category(self):
        cat = CategoryCreate(name="Cakes", slug="cakes")
        assert cat.slug == "cakes"

    def test_slug_with_numbers_valid(self):
        cat = CategoryCreate(name="Cakes", slug="custom-cakes-123")
        assert cat.slug == "custom-cakes-123"

    def test_slug_uppercase_invalid(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="Cakes", slug="CAKES")

    def test_slug_with_spaces_invalid(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="Cakes", slug="my cakes")

    def test_slug_with_special_chars_invalid(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="Cakes", slug="cakes@special!")

    def test_name_required(self):
        with pytest.raises(ValidationError):
            CategoryCreate(slug="cakes")

    def test_default_display_order(self):
        cat = CategoryCreate(name="Cakes", slug="cakes")
        assert cat.display_order == 0
