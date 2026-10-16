"""Busy mode on the wire — what the register's banner and switch read and send."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class BusyChannelResponse(BaseModel):
    """One aggregator outlet's progress, drawn as a chip on the banner."""

    channel: str
    label: str
    #: `closing` / `closed` / `opening` / `open` — the write is on its way or
    #: done; `failed` — the last try failed and another is scheduled (`error`
    #: says why); `manual` — we cannot write this outlet, staff close it in the
    #: partner app; `not_live` — portal writes are switched off server-side.
    state: Literal[
        "closing", "closed", "opening", "open", "failed", "manual", "not_live"
    ]
    error: str | None = None


class BusyStatusResponse(BaseModel):
    busy: bool
    since: datetime | None = None
    #: Who turned it on, as the register names staff.
    by_name: str | None = None
    channels: list[BusyChannelResponse] = []


class SetBusyRequest(BaseModel):
    busy: bool
