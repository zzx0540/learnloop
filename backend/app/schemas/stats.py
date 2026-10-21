import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class HeartbeatCreate(BaseModel):
    course_id: uuid.UUID
    kind: Literal["learn", "review", "qa"]


class HeartbeatOut(BaseModel):
    today_study_seconds: int


class DailyStatOut(BaseModel):
    date: date
    study_seconds: int
    review_count: int
    learn_items_done: int


class StatsMeOut(BaseModel):
    days: list[DailyStatOut]
    streak: int


class CompletedItemOut(BaseModel):
    title: str
    type: str
    course_name: str
    completed_at: datetime
    actual_minutes: int | None
