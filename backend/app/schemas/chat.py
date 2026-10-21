import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ChatContext(BaseModel):
    mode: Literal["learning", "review", "general"] = "general"
    plan_item_id: uuid.UUID | None = None


class ChatCreate(BaseModel):
    agent: Literal["planner", "companion"]
    context: ChatContext | None = None


class ChatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    agent: str
    title: str
    context: dict
    created_at: datetime
    last_message_at: datetime


class ChatMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content_md: str
    images: list
    scope_class: str | None
    created_at: datetime
