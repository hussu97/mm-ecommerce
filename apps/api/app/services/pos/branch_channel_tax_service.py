"""Read and replace a branch's per-channel VAT / trade-license configs.

The admin edits these as a small grid (one row per channel class); this is the
list-upsert behind that screen, mirroring `branch_hours_service.set_weekly`.
Resolution of a config into a decision an order writer uses lives elsewhere, in
`services/orders/tax_identity_service` — this module only manages the rows.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch_channel_tax_config import BranchChannelTaxConfig


async def list_configs(
    db: AsyncSession, branch_id: uuid.UUID
) -> list[BranchChannelTaxConfig]:
    rows = (
        (
            await db.execute(
                select(BranchChannelTaxConfig)
                .where(BranchChannelTaxConfig.branch_id == branch_id)
                .order_by(BranchChannelTaxConfig.channel_class)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def replace_configs(
    db: AsyncSession, branch_id: uuid.UUID, configs: Sequence
) -> list[BranchChannelTaxConfig]:
    """Upsert the given rows by `channel_class`; delete any channel omitted.

    A channel with no row inherits the branch/business identity and is
    VAT-registered — the same behaviour as before this feature — so removing a
    row is how an operator returns a channel to the default. `flush()` only; the
    request-scoped `get_db` commits.
    """
    existing = {c.channel_class: c for c in await list_configs(db, branch_id)}
    wanted = {c.channel_class: c for c in configs}

    for channel_class, payload in wanted.items():
        row = existing.get(channel_class)
        if row is None:
            row = BranchChannelTaxConfig(
                branch_id=branch_id, channel_class=channel_class
            )
            db.add(row)
        row.legal_entity_id = payload.legal_entity_id
        row.tax_group_id = payload.tax_group_id
        row.is_active = payload.is_active

    for channel_class, row in existing.items():
        if channel_class not in wanted:
            await db.delete(row)

    await db.flush()
    return await list_configs(db, branch_id)
