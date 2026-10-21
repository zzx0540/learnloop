import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReviewAnswerCreate(BaseModel):
    rating: int = Field(ge=1, le=4)


class ReviewKpOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    summary: str
    recall_prompt: str
    primary_wiki_slug: str | None
    status: str


class ReviewVocabOut(BaseModel):
    id: uuid.UUID
    word: str
    phonetic: str
    meaning_md: str
    example_md: str


class ReviewIntervalsOut(BaseModel):
    again_days: float
    hard_days: float
    good_days: float
    easy_days: float


class ReviewQueueCardOut(BaseModel):
    card_id: uuid.UUID
    due: datetime
    state: int
    reps: int
    kp: ReviewKpOut | None
    vocab: ReviewVocabOut | None
    intervals: ReviewIntervalsOut


class ReviewQueueOut(BaseModel):
    cards: list[ReviewQueueCardOut]
    due_count: int
    reviewed_today: int
    activated_today: int


class ReviewCardStateOut(BaseModel):
    id: uuid.UUID
    stability: float
    difficulty: float
    due: datetime
    state: int
    reps: int
    lapses: int
    last_review: datetime | None


class ReviewAnswerOut(BaseModel):
    card: ReviewCardStateOut
    kp_status: str | None
    next_due: datetime
