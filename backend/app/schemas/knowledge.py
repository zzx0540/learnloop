import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class KnowledgePointOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    summary: str
    primary_wiki_page_id: uuid.UUID | None
    importance: int
    difficulty: int
    status: str
    mastery_score: int
    projected_basic_at: date | None
    projected_full_at: date | None
    created_at: datetime


class KnowledgePointUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    importance: int | None = Field(default=None, ge=1, le=5)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    status: Literal["archived"] | None = None


class GraphNodeOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    summary: str
    status: str
    mastery_score: int
    importance: int
    difficulty: int
    projected_basic_at: date | None
    projected_full_at: date | None
    primary_wiki_page_id: uuid.UUID | None


class GraphEdgeOut(BaseModel):
    id: uuid.UUID
    source: uuid.UUID
    target: uuid.UUID
    relation: str


class GraphOut(BaseModel):
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]
