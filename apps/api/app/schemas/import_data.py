from __future__ import annotations

from pydantic import BaseModel


class ImportError(BaseModel):
    row: int
    message: str


class ImportResult(BaseModel):
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[ImportError] = []
