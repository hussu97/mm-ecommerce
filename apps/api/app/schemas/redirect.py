from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: A site path: leading slash, no scheme, no host, no fragment.
#:
#: Anchored at both ends so a pasted `https://meltingmomentscakes.com/brownies`
#: is rejected here rather than becoming a redirect to a relative URL that
#: resolves against whichever page the visitor was on. Query strings are
#: excluded too: the resolver matches on the path and carries the visitor's own
#: query across, so one baked into the rule would be silently dropped.
PATH_PATTERN = r"^/[A-Za-z0-9\-._~/%]*$"


def _normalise(path: str) -> str:
    """
    The one spelling of a path this table stores.

    Lowercased, trailing slash removed, so `/Cat-Brownies/` and `/cat-brownies`
    are the same row rather than two rows the operator has to remember to keep
    in step. The middleware normalises the incoming path the same way before
    looking it up, which is the whole point — a rule only fires if both ends
    agree on what the path is called.
    """
    cleaned = path.strip().lower().rstrip("/")
    return cleaned or "/"


class RedirectBase(BaseModel):
    from_path: str = Field(max_length=500, pattern=PATH_PATTERN)
    to_path: str = Field(max_length=500, pattern=PATH_PATTERN)
    is_prefix: bool = False
    status_code: Literal[301, 308] = 308
    is_active: bool = True
    note: str | None = None

    @field_validator("from_path", "to_path")
    @classmethod
    def normalise_paths(cls, v: str) -> str:
        return _normalise(v)


class RedirectCreate(RedirectBase):
    pass


class RedirectUpdate(BaseModel):
    from_path: str | None = Field(None, max_length=500, pattern=PATH_PATTERN)
    to_path: str | None = Field(None, max_length=500, pattern=PATH_PATTERN)
    is_prefix: bool | None = None
    status_code: Literal[301, 308] | None = None
    is_active: bool | None = None
    note: str | None = None

    @field_validator("from_path", "to_path")
    @classmethod
    def normalise_paths(cls, v: str | None) -> str | None:
        return None if v is None else _normalise(v)


class RedirectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    from_path: str
    to_path: str
    is_prefix: bool
    status_code: int
    is_active: bool
    source: str
    note: str | None
    hit_count: int
    last_hit_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RedirectRule(BaseModel):
    """
    One rule as the storefront's middleware wants it.

    Deliberately not `RedirectResponse`: this is fetched on a cold edge instance
    and held in memory, so it carries the four fields the match needs and none of
    the console's bookkeeping. At eight-plus rows the difference is small; the
    reason to keep it small is that it is the shape a public, uncached-by-anyone
    endpoint hands to every deployment region.
    """

    from_path: str
    to_path: str
    is_prefix: bool
    status_code: int


class RedirectMap(BaseModel):
    rules: list[RedirectRule]


class RedirectHit(BaseModel):
    """What the middleware reports after it has already sent the redirect."""

    from_path: str = Field(max_length=500, pattern=PATH_PATTERN)

    @field_validator("from_path")
    @classmethod
    def normalise_path(cls, v: str) -> str:
        return _normalise(v)
