from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.services.customer_service import _components, _Source


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
