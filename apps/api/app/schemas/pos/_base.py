"""Shared building blocks for the POS schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Translations = dict[str, dict[str, str]]

OrderTypeLiteral = Literal["dine_in", "pickup", "delivery", "drive_thru"]

#: `HH:MM`, 24-hour. Used by every opening-hours and window field here.
_TIME_RE = r"^([01]\d|2[0-3]):[0-5]\d$"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
