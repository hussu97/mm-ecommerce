"""Resolve the VAT treatment and trade-license identity for one order.

A branch can trade under different licenses per sales channel — Barsha's counter
is not VAT-registered while its website and aggregator sales are, under the
Melting Moments license. This service is the one place `Order.source` maps to a
channel class and the `branch_channel_tax_configs` row (if any) is turned into a
concrete decision the write paths freeze onto the order.

The three order writers (website `order_service`, counter
`pos_order_service.recalculate`, aggregator `promote`/`grubops_orders_service`)
all call `resolve()` and then, when `not vat_registered`, force VAT to zero and
`stamp_identity()` onto the order. A branch/channel with no active row resolves
to VAT-registered + inherited identity, so nothing changes for branches that
trade under one identity.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.branch import Branch
from app.models.branch_channel_tax_config import (
    BranchChannelTaxConfig,
    ChannelClassEnum,
)
from app.models.business_settings import BusinessSettings

#: `Order.source` → channel class. The one place the mapping is decided; anything
#: unexpected falls to `website` (the VAT-registered default), never to a
#: silently non-registered class.
CHANNEL_CLASS_BY_SOURCE: dict[str, str] = {
    "cashier": ChannelClassEnum.COUNTER.value,
    "online": ChannelClassEnum.WEBSITE.value,
    "aggregator": ChannelClassEnum.AGGREGATOR.value,
}


def channel_class_for(source: str | None) -> str:
    return CHANNEL_CLASS_BY_SOURCE.get(source or "", ChannelClassEnum.WEBSITE.value)


@dataclass(frozen=True)
class TaxIdentity:
    """The VAT treatment and identity resolved for one (branch, channel).

    `vat_registered` drives whether VAT is charged at all. The identity fields
    are what a receipt/invoice/report should print for this order; each is null
    when nothing overrides the branch/business default, and every reader falls
    back to `Branch`/`BusinessSettings` on null. `tax_group_id` is an optional
    per-channel VAT-group override — null leaves the existing per-product/branch
    tax-group resolution unchanged.
    """

    vat_registered: bool
    tax_group_id: uuid.UUID | None
    tax_number: str | None
    tax_registration_name: str | None
    invoice_title: str | None


#: What a branch with no configured row resolves to: registered, everything
#: inherited. Behaviourally identical to the pre-feature codebase.
_DEFAULT_IDENTITY = TaxIdentity(
    vat_registered=True,
    tax_group_id=None,
    tax_number=None,
    tax_registration_name=None,
    invoice_title=None,
)


async def resolve(
    db: AsyncSession, *, branch_id: uuid.UUID | None, source: str | None
) -> TaxIdentity:
    """The tax identity for an order on `branch_id` from channel `source`.

    No branch, or no active config row → the default (registered, inherited), so
    the caller stamps nothing new and behaves exactly as today. A row present →
    its `vat_registered`, with each identity field falling back per-field to the
    branch's `tax_number`/`tax_registration_name` and the business
    `invoice_title` when the row leaves it null. That lets a row flip only
    `vat_registered` without re-typing the inherited identity.
    """
    if branch_id is None:
        return _DEFAULT_IDENTITY

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
        return _DEFAULT_IDENTITY

    branch = await db.get(Branch, branch_id)
    # A non-VAT-registered entity has no TRN, so its channel never inherits the
    # branch's — the branch's TRN belongs to the registered entity its other
    # channels trade under, and printing it here would put a real TRN on a
    # document that must carry none. A registered channel inherits it as before.
    if config.vat_registered:
        tax_number = config.tax_number or (branch.tax_number if branch else None)
    else:
        tax_number = config.tax_number
    tax_registration_name = config.tax_registration_name or (
        branch.tax_registration_name if branch else None
    )
    invoice_title = config.invoice_title
    if invoice_title is None:
        if config.vat_registered:
            settings = (
                (await db.execute(select(BusinessSettings).limit(1))).scalars().first()
            )
            invoice_title = settings.invoice_title if settings else None
        else:
            # A business that is not VAT-registered may not issue a "Tax Invoice";
            # default its document to a plain "Invoice" unless the config names one.
            invoice_title = "Invoice"

    return TaxIdentity(
        vat_registered=config.vat_registered,
        tax_group_id=config.tax_group_id,
        tax_number=tax_number,
        tax_registration_name=tax_registration_name,
        invoice_title=invoice_title,
    )


def stamp_identity(order, identity: TaxIdentity) -> None:
    """Freeze the resolved identity onto the order.

    Always called (even for the inherited default, which writes nulls), so the
    order row records the decision that was in force when it was issued.
    """
    order.tax_number = identity.tax_number
    order.tax_registration_name = identity.tax_registration_name
    order.invoice_title = identity.invoice_title
