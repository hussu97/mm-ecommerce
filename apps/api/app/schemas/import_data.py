from __future__ import annotations

from pydantic import BaseModel, Field


class ImportError(BaseModel):
    row: int
    message: str


class ImportResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[ImportError] = []
    # Every image URL the import attached to a row, so the endpoint can warm the
    # storefront's derivatives for them. Excluded from the response: it is a
    # handover between two server-side steps, not something the console shows.
    image_urls: list[str] = Field(default_factory=list, exclude=True)
