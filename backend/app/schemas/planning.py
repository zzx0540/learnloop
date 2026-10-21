import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.course import CourseOut


class PlanItemKpOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str


class PlanItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    scheduled_date: date
    type: str
    title: str
    description_md: str
    est_minutes: int
    status: str
    completed_at: datetime | None
    actual_minutes: int | None
    knowledge_points: list[PlanItemKpOut] = Field(default_factory=list)


class PlanItemStudyKpOut(BaseModel):
    id: uuid.UUID
    slug: str
    title: str
    summary: str
    recall_prompt: str
    primary_wiki_slug: str | None
    status: str


class PlanItemStudyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    plan_id: uuid.UUID
    course_id: uuid.UUID
    seq: int
    scheduled_date: date
    type: str
    title: str
    description_md: str
    est_minutes: int
    status: str
    completed_at: datetime | None
    actual_minutes: int | None
    kps: list[PlanItemStudyKpOut]


class PlanDayOut(BaseModel):
    date: date
    items: list[PlanItemOut]


class PlanDetailOut(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    goal_summary: str
    daily_minutes: int
    start_date: date
    end_date_estimate: date | None
    projected_basic_date: date | None
    projected_full_date: date | None
    rationale_md: str
    days: list[PlanDayOut]


class PlanItemUpdate(BaseModel):
    status: Literal["in_progress", "done", "skipped"]
    actual_minutes: int | None = Field(default=None, ge=0, le=1440)


class ActivePlanSummary(BaseModel):
    id: uuid.UUID
    version: int
    start_date: date
    end_date_estimate: date | None
    daily_minutes: int
    rationale_md: str


class KpStatsOut(BaseModel):
    total: int
    by_status: dict[str, int]


class TodayQuizOut(BaseModel):
    quiz_id: uuid.UUID
    status: str
    my_attempt_status: str | None
    total_score: float | None
    max_score: float | None


class CourseOverviewOut(BaseModel):
    course: CourseOut
    active_plan: ActivePlanSummary | None
    kp_stats: KpStatsOut
    projected_basic_date: date | None
    projected_full_date: date | None
    assumption_text: str
    latest_progress_summary_md: str | None
    open_weakness_count: int
    today_quiz: TodayQuizOut | None
