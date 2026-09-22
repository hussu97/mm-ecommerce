from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.services.customer_service import (
    _components,
    _Source,
    _uae_coordinates,
    normalise_customer_name,
)


def _source(
    key: str,
    *,
    name: str | None = "Aisha Khan",
    email: str | None = None,
    phone: str | None = None,
    user_id: uuid.UUID | None = None,
) -> _Source:
    return _Source(
        key=key,
        name=name,
        email=email,
        phone=phone,
        phone_country="AE" if phone else None,
        user_id=user_id,
        observed_at=datetime(2026, 9, 20, tzinfo=UTC),
    )


def test_components_merge_same_name_and_phone_across_channels():
    groups = list(
        _components(
            [
                _source("order:website", phone="+971501234567"),
                _source(
                    "order:talabat", phone="+971501234567", email="aisha@example.com"
                ),
            ]
        )
    )

    assert [[source.key for source in group] for group in groups] == [
        ["order:website", "order:talabat"]
    ]


def test_customer_name_is_canonicalised_before_identity_matching_and_display():
    assert normalise_customer_name("  aISHA   kHAN ") == "Aisha Khan"
    groups = list(
        _components(
            [
                _source("order:website", name="aisha khan", phone="+971501234567"),
                _source("order:counter", name="AISHA KHAN", phone="+971501234567"),
            ]
        )
    )

    assert [[source.key for source in group] for group in groups] == [
        ["order:website", "order:counter"]
    ]


def test_components_do_not_merge_shared_phone_when_names_differ():
    groups = list(
        _components(
            [
                _source("order:aisha", name="Aisha Khan", phone="+971501234567"),
                _source("order:omar", name="Omar Khan", phone="+971501234567"),
            ]
        )
    )

    assert {tuple(source.key for source in group) for group in groups} == {
        ("order:aisha",),
        ("order:omar",),
    }


def test_components_merge_same_name_and_email_without_phone():
    groups = list(
        _components(
            [
                _source("order:website", email="Aisha@Example.com"),
                _source("order:counter", email="aisha@example.com"),
            ]
        )
    )

    assert [[source.key for source in group] for group in groups] == [
        ["order:website", "order:counter"]
    ]


def test_components_link_a_registered_account_to_its_orders_without_a_name():
    account_id = uuid.uuid4()
    groups = list(
        _components(
            [
                _source(
                    "user:1", name=None, email="aisha@example.com", user_id=account_id
                ),
                _source(
                    "order:1",
                    name="Aisha Khan",
                    email="aisha@example.com",
                    user_id=account_id,
                ),
            ]
        )
    )

    assert [[source.key for source in group] for group in groups] == [
        ["user:1", "order:1"]
    ]


def test_delivery_area_cache_accepts_both_website_and_marketplace_coordinate_spellings():
    assert _uae_coordinates({"latitude": "25.2048", "longitude": "55.2708"}) == (
        Decimal("25.2048"),
        Decimal("55.2708"),
    )
    assert _uae_coordinates({"lat": 25.2048, "lng": 55.2708}) == (
        Decimal("25.2048"),
        Decimal("55.2708"),
    )
    assert _uae_coordinates({"lat": 250045035, "lng": 551324881}) == (
        Decimal("25.0045035"),
        Decimal("55.1324881"),
    )
    assert _uae_coordinates({"latitude": 51.5072, "longitude": -0.1276}) is None
