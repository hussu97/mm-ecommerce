"""Per-(branch, sales-channel) legal-entity mapping."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, model_validator

from ._base import ORMModel

ChannelClassLiteral = Literal["counter", "website", "aggregator"]


class BranchChannelTaxConfigBase(BaseModel):
    channel_class: ChannelClassLiteral
    #: The legal entity this channel trades under — its `vat_registered` decides
    #: whether VAT is charged, and its brand/TRN/title/logo are what the receipt
    #: shows.
    legal_entity_id: UUID
    #: Optional per-channel VAT-group override. Null keeps the existing
    #: per-product/per-branch tax-group resolution.
    tax_group_id: UUID | None = None
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
    legal_entity_id: UUID
    tax_group_id: UUID | None
    is_active: bool
    created_at: datetime
    updated_at: datetime
