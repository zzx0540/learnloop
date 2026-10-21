import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CourseKind = Literal["knowledge", "language", "programming"]
LanguageTrack = Literal["ielts", "cet6", "postgraduate", "new_concept", "custom"]


class CourseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    goal_text: str = Field(default="", max_length=2000)
    daily_minutes: int = Field(default=45, ge=5, le=600)
    target_date: date | None = None
    kind: CourseKind = "knowledge"
    language_track: LanguageTrack | None = None

    @model_validator(mode="after")
    def language_track_matches_kind(self):
        if self.kind != "language" and self.language_track is not None:
            raise ValueError("仅英语课程可以设置课程轨道")
        return self


class CourseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    goal_text: str | None = Field(default=None, max_length=2000)
    daily_minutes: int | None = Field(default=None, ge=5, le=600)
    target_date: date | None = None
    status: str | None = Field(default=None, pattern=r"^(onboarding|active|completed|archived)$")
    kind: CourseKind | None = None
    language_track: LanguageTrack | None = None

    @field_validator("kind")
    @classmethod
    def kind_cannot_be_null(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("课程类型不能为空")
        return value


class CourseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    goal_text: str
    status: str
    kind: CourseKind
    language_track: LanguageTrack | None = None
    daily_minutes: int
    target_date: date | None
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_name: str
    size_bytes: int
    status: str
    page_count: int | None
    error: str | None
    created_at: datetime


class WikiPageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    kind: str
    content_md: str
    out_links: list[str]
    source_refs: list
    version: int
    updated_by: str
    updated_at: datetime


class WikiPageBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    kind: str
    version: int
    updated_at: datetime


class WikiRevisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    version: int
    content_md: str
    change_summary: str
    created_at: datetime


class WikiLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    op: str
    entry_md: str
    created_at: datetime


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    status: str
    result: dict | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
