import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.course import CourseKind


class ManifestFileV1(BaseModel):
    relative_path: str = Field(min_length=1, max_length=512)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ManifestDocumentV1(BaseModel):
    key: str = Field(min_length=1, max_length=32)
    original_name: str = Field(min_length=1, max_length=256)
    mime: str = Field(max_length=128)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    stored_rel: str = Field(min_length=1, max_length=512)
    parsed_rel: str | None = Field(default=None, max_length=512)
    page_count: int | None = Field(default=None, ge=0)


class ManifestDocumentImageV1(BaseModel):
    key: str = Field(min_length=1, max_length=32)
    document_key: str | None = Field(default=None, max_length=32)
    stored_rel: str = Field(min_length=1, max_length=512)
    caption: str = Field(default="", max_length=4000)


class ManifestWikiPageV1(BaseModel):
    slug: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    kind: str = Field(min_length=1, max_length=24)
    content_md: str
    out_links: list[str]
    source_refs: list[dict]


class ManifestKnowledgePointV1(BaseModel):
    slug: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=256)
    summary: str
    recall_prompt: str = Field(default="", max_length=200)
    primary_wiki_slug: str | None = Field(default=None, max_length=128)
    importance: int = Field(ge=1, le=5)
    difficulty: int = Field(ge=1, le=5)
    created_by: str = Field(default="planner", max_length=16)


class ManifestKpEdgeV1(BaseModel):
    src_slug: str = Field(min_length=1, max_length=120)
    dst_slug: str = Field(min_length=1, max_length=120)
    relation: Literal["prerequisite", "related", "part_of", "example_of"]
    created_by: str = Field(default="planner", max_length=16)


class ManifestKpWikiLinkV1(BaseModel):
    kp_slug: str = Field(min_length=1, max_length=120)
    wiki_slug: str = Field(min_length=1, max_length=128)
    role: str = Field(default="mention", max_length=16)


class ManifestVocabEntryV1(BaseModel):
    word: str = Field(min_length=1, max_length=64)
    phonetic: str = Field(default="", max_length=64)
    meaning_md: str = Field(min_length=1)
    example_md: str = ""
    source_note: str = Field(default="", max_length=120)


class ManifestCourseV1(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str
    goal_text: str = Field(max_length=2000)
    kind: CourseKind
    daily_minutes: int = Field(ge=5, le=600)
    daily_new_words: int | None = Field(default=None, ge=5, le=100)


class MarketCourseManifestV1(BaseModel):
    schema_version: Literal[1] = 1
    course: ManifestCourseV1
    files: list[ManifestFileV1]
    documents: list[ManifestDocumentV1]
    document_images: list[ManifestDocumentImageV1]
    wiki_pages: list[ManifestWikiPageV1]
    knowledge_points: list[ManifestKnowledgePointV1]
    kp_edges: list[ManifestKpEdgeV1]
    kp_wiki_links: list[ManifestKpWikiLinkV1]
    vocab_entries: list[ManifestVocabEntryV1]


class MarketCourseCreate(BaseModel):
    source_course_id: uuid.UUID
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=2000)
    price_points: int = Field(default=0, ge=0, le=1_000_000)


class MarketCourseUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    price_points: int | None = Field(default=None, ge=0, le=1_000_000)

    @field_validator("title", "description", "price_points")
    @classmethod
    def fields_cannot_be_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("字段不能为空")
        return value


class MarketSourceCourseOut(BaseModel):
    id: uuid.UUID
    name: str
    owner_username: str
    kind: CourseKind
    status: str
    document_count: int
    is_owned_by_admin: bool


class MarketCourseAdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_course_id: uuid.UUID | None
    source_course_name: str | None
    source_owner_username: str | None
    source_owned_by_admin: bool
    title: str
    description: str
    price_points: int
    status: str
    current_version: int
    version_status: str | None
    document_count: int
    knowledge_point_count: int
    vocab_count: int
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MarketPublishOut(BaseModel):
    job_id: uuid.UUID
    version: int


class MarketCourseOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    kind: CourseKind
    price_points: int
    current_version: int
    document_count: int
    knowledge_point_count: int
    vocab_count: int
    acquisition_id: uuid.UUID | None
    acquisition_status: str | None
    owned_course_id: uuid.UUID | None
    acquisition_error: str | None
    published_at: datetime | None


class MarketCourseListOut(BaseModel):
    items: list[MarketCourseOut]
    total: int


class MarketAcquisitionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    market_course_id: uuid.UUID
    target_course_id: uuid.UUID | None
    title_snapshot: str
    price_snapshot: int
    version_snapshot: int
    status: str
    error: str | None
    job_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
