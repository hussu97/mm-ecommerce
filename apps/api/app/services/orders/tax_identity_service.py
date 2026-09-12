"""Resolve the legal entity (trade licence) an order is issued under.

A branch can trade under more than one licence per sales channel — Barsha's
counter is Najm AlShamal (not VAT-registered) while its website and aggregator
sales are Fatema Cake Sweets (registered). This service is the one place
`Order.source` maps to a channel class and the `branch_channel_tax_configs` row
turns into the `LegalEntity` the write paths freeze onto the order.

The three order writers (website `order_service`, counter
`pos_order_service.recalculate`, aggregator `promote`/`grubops_orders_service`)
call `resolve()`, force VAT to zero when the entity is `not vat_registered`, and
`stamp()` the entity id onto the order. A branch/channel with no config row falls
back to the registered default entity, so every order is attributed.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch_channel_tax_config import (
    BranchChannelTaxConfig,
    ChannelClassEnum,
)
from app.models.legal_entity import LegalEntity

#: `Order.source` → channel class. The one place the mapping is decided; anything
#: unexpected falls to `website` (a registered channel), never to a silently
#: non-registered one.
CHANNEL_CLASS_BY_SOURCE: dict[str, str] = {
    "cashier": ChannelClassEnum.COUNTER.value,
    "online": ChannelClassEnum.WEBSITE.value,
    "aggregator": ChannelClassEnum.AGGREGATOR.value,
}

#: The registered entity every order falls back to when a branch/channel has no
#: config row — seeded by migration `237` in every environment.
DEFAULT_ENTITY_REFERENCE = "fatema"


def channel_class_for(source: str | None) -> str:
    return CHANNEL_CLASS_BY_SOURCE.get(source or "", ChannelClassEnum.WEBSITE.value)


async def _default_entity(db: AsyncSession) -> LegalEntity | None:
    return (
        await db.execute(
            select(LegalEntity).where(LegalEntity.reference == DEFAULT_ENTITY_REFERENCE)
        )
    ).scalar_one_or_none()


async def resolve(
    db: AsyncSession, *, branch_id: uuid.UUID | None, source: str | None
) -> LegalEntity | None:
    """The legal entity for an order on `branch_id` from channel `source`.

    The `(branch, channel)` config names the entity; with no branch or no active
    config row it falls back to the registered default (Fatema). `None` only when
    even the default is missing (a database seeded before `237` — not a state a
    deployed environment reaches), which the callers treat as VAT-registered and
    stamp nothing.
    """
    if branch_id is None:
        return await _default_entity(db)

    channel_class = channel_class_for(source)
    config = (
        await db.execute(
            select(BranchChannelTaxConfig).where(
                BranchChannelTaxConfig.branch_id == branch_id,
                BranchChannelTaxConfig.channel_class == channel_class,
                BranchChannelTaxConfig.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if config is None:
        return await _default_entity(db)
    return await db.get(LegalEntity, config.legal_entity_id)


def is_vat_registered(entity: LegalEntity | None) -> bool:
    """Whether VAT should be charged. A missing entity is treated as registered,
    so a resolution gap never silently drops VAT."""
    return entity.vat_registered if entity is not None else True


def stamp(order, entity: LegalEntity | None) -> None:
    """Freeze the resolved entity onto the order."""
    if entity is not None:
        order.legal_entity_id = entity.id
