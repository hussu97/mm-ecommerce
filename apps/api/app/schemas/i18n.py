from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LanguageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    native_name: str
    direction: str
    is_default: bool
    is_active: bool
    display_order: int


class LanguageCreate(BaseModel):
    code: str = Field(min_length=2, max_length=10)
    name: str = Field(min_length=1, max_length=100)
    native_name: str = Field(min_length=1, max_length=100)
    direction: str = Field(default="ltr", pattern=r"^(ltr|rtl)$")
    is_active: bool = True
    display_order: int = 0


class LanguageUpdate(BaseModel):
    name: str | None = None
    native_name: str | None = None
    direction: str | None = Field(None, pattern=r"^(ltr|rtl)$")
    is_active: bool | None = None
    display_order: int | None = None


class TranslationEntry(BaseModel):
    key: str
    value: str


class TranslationBulkUpsert(BaseModel):
    namespace: str = Field(min_length=1, max_length=50)
    translations: list[TranslationEntry]


class UiTranslationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    locale: str
    namespace: str
    key: str
    value: str
