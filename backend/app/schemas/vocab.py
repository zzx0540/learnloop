import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VocabItemOut(BaseModel):
    word: str = Field(min_length=1, max_length=64)
    phonetic: str = Field(default="", max_length=64)
    meaning_md: str = Field(min_length=1, max_length=500)
    example_md: str = Field(default="", max_length=500)


class NormalizeVocabOut(BaseModel):
    items: list[VocabItemOut] = Field(max_length=60)


class VocabImportJobOut(BaseModel):
    job_id: uuid.UUID
    source_id: uuid.UUID


class VocabCardSummaryOut(BaseModel):
    stability: float
    due: datetime
    state: int


class VocabEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    word: str
    phonetic: str
    meaning_md: str
    example_md: str
    status: Literal["pending", "active", "archived"]
    activated_at: datetime | None
    source_note: str
    created_at: datetime
    card: VocabCardSummaryOut | None = None


class VocabStatsOut(BaseModel):
    total: int
    pending: int
    active: int
    archived: int
    mastered: int
    daily_new_words: int
    projected_days: int


class VocabSettingsUpdate(BaseModel):
    daily_new_words: int = Field(ge=5, le=100)


class VocabArchiveUpdate(BaseModel):
    status: Literal["archived"]
