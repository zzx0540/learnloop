import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class AttemptSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: str
    started_at: datetime
    deadline_at: datetime | None
    auto_submitted: bool
    submitted_at: datetime | None
    graded_at: datetime | None
    total_score: float | None
    max_score: float | None


class QuizListItemOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    scheduled_date: date
    status: str
    question_count: int
    time_limit_seconds: int | None
    my_attempt: AttemptSummaryOut | None


class QuestionPublicOut(BaseModel):
    id: uuid.UUID
    seq: int
    qtype: str
    stem_md: str
    options: list


class AttemptAnswerOut(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    answer: dict
    is_correct: bool | None
    score: float | None
    feedback_md: str
    graded_by: str
    graded_at: datetime | None
    updated_at: datetime


class QuestionResultOut(QuestionPublicOut):
    answer: dict
    explanation_md: str
    source_refs: list
    my_answer: AttemptAnswerOut | None


class QuizPublicOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    kind: str
    title: str
    scheduled_date: date
    status: str
    plan_item_id: uuid.UUID | None
    time_limit_seconds: int | None
    my_attempt: AttemptSummaryOut | None
    questions: list[QuestionPublicOut]


class QuizResultOut(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    kind: str
    title: str
    scheduled_date: date
    status: str
    plan_item_id: uuid.UUID | None
    time_limit_seconds: int | None
    my_attempt: AttemptSummaryOut
    questions: list[QuestionResultOut]


class AttemptCreateOut(AttemptSummaryOut):
    quiz_id: uuid.UUID | None
    kind: str
    server_now: datetime


class SubmitAttemptOut(AttemptSummaryOut):
    grading_job_id: uuid.UUID | None


class AttemptDetailOut(AttemptCreateOut):
    answers: list[AttemptAnswerOut]


class BookExamIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    exam_date: date | None = Field(default=None, alias="date")


class ExamEventCreate(BaseModel):
    kind: str


class ExamEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: str
    created_at: datetime


class WeaknessOut(BaseModel):
    id: uuid.UUID
    kp_id: uuid.UUID
    kp_slug: str
    kp_title: str
    kind: str
    severity: int
    status: str
    seen_count: int
    correct_streak: int
    description: str
    last_seen_at: datetime
    created_at: datetime
