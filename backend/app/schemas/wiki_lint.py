import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class WikiLintFindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    page_id: uuid.UUID | None
    page_slug: str | None
    page_title: str | None
    kind: str
    detail_md: str
    status: str
    created_at: datetime


class WikiLintFindingUpdate(BaseModel):
    status: Literal["resolved", "dismissed"]
