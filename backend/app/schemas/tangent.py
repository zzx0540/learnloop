import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

TangentStatus = Literal["parked", "planned", "answered", "dismissed"]


class TangentUpdate(BaseModel):
    status: TangentStatus


class TangentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    chat_message_id: uuid.UUID | None
    question: str
    brief_answer: str
    status: str
    created_at: datetime
