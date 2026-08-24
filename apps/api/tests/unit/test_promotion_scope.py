"""
`Promotion.matches_order` — the "empty array means everything" scope rule that
keeps a counter-only promotion off the storefront and the aggregators.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.base import utcnow
from app.models.marketing import Promotion


def _promo(**overrides) -> Promotion:
    fields = dict(
        name="Counter 15% Off",
        type="basic",
        trigger="spend",
        trigger_value=Decimal("0"),
        reward="percentage_off_order",
        reward_value=Decimal("15"),
        branch_ids=[],
        order_types=[],
        sources=["cashier"],
        auto_apply=True,
        priority=100,
        is_active=True,
        deleted_at=None,
    )
    fields.update(overrides)
    return Promotion(**fields)


def test_matches_order_channel_gate():
    promo = _promo(sources=["cashier"])
    assert promo.matches_order(source="cashier", branch_id=None, order_type=None)
    assert not promo.matches_order(source="online", branch_id=None, order_type=None)


def test_empty_sources_means_every_channel():
    promo = _promo(sources=[])
    assert promo.matches_order(source="online", branch_id=None, order_type=None)


def test_branch_and_type_gates():
    branch = uuid.uuid4()
    promo = _promo(branch_ids=[branch], order_types=["pickup"])
    assert promo.matches_order(source="cashier", branch_id=branch, order_type="pickup")
    assert not promo.matches_order(
        source="cashier", branch_id=uuid.uuid4(), order_type="pickup"
    )
    assert not promo.matches_order(
        source="cashier", branch_id=branch, order_type="delivery"
    )


def test_inactive_or_deleted_never_matches():
    assert not _promo(is_active=False).matches_order(
        source="cashier", branch_id=None, order_type=None
    )
    assert not _promo(deleted_at=utcnow()).matches_order(
        source="cashier", branch_id=None, order_type=None
    )
