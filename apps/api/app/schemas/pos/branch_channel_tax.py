"""Per-(branch, sales-channel) VAT and trade-license configuration."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from ._base import ORMModel

ChannelClassLiteral = Literal["counter", "website", "aggregator"]


class BranchChannelTaxConfigBase(BaseModel):
    channel_class: ChannelClassLiteral
    #: When false, this channel charges no VAT — the order stamps zero VAT and
    #: prints no VAT line, regardless of the products' tax groups.
    vat_registered: bool = True
    #: Optional per-channel VAT-group override. Null keeps the existing
    #: per-product/per-branch tax-group resolution.
    tax_group_id: UUID | None = None
    #: Identity to print. Null inherits the branch's `tax_number` /
    #: `tax_registration_name` and the business `invoice_title`.
    tax_number: str | None = Field(None, max_length=50)
    tax_registration_name: str | None = Field(None, max_length=200)
    invoice_title: str | None = Field(None, max_length=120)
    is_active: bool = True


class BranchChannelTaxConfigUpsert(BranchChannelTaxConfigBase):
    """One row in the list-upsert PUT — keyed by `channel_class`."""


class BranchChannelTaxConfigsUpdate(BaseModel):
    """Replace a branch's whole set of channel tax configs.

    A list-upsert: the rows present are created/updated by `channel_class`, and a
    channel omitted from the list is removed (falls back to inherited behaviour).
    At most one row per channel class.
    """

    configs: list[BranchChannelTaxConfigUpsert]

    @model_validator(mode="after")
    def _one_row_per_channel(self) -> "BranchChannelTaxConfigsUpdate":
        seen: set[str] = set()
        for cfg in self.configs:
            if cfg.channel_class in seen:
                raise ValueError(
                    f"channel_class {cfg.channel_class!r} appears more than once"
                )
            seen.add(cfg.channel_class)
        return self


class BranchChannelTaxConfigResponse(ORMModel):
    id: UUID
    branch_id: UUID
    channel_class: str
    vat_registered: bool
    tax_group_id: UUID | None
    tax_number: str | None
    tax_registration_name: str | None
    invoice_title: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
