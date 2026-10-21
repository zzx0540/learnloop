import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PointsSummaryOut(BaseModel):
    balance: int
    streak: int
    today_multiplier: float
    today_earned: int


class PointsLedgerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID | None
    kind: str
    amount: int
    multiplier: float
    note: str
    ref_id: uuid.UUID | None
    created_at: datetime


class RewardCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=500)
    cost: int = Field(gt=0)
    is_active: bool = True


class RewardUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=500)
    cost: int | None = Field(default=None, gt=0)
    is_active: bool | None = None


class RewardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str
    cost: int
    is_active: bool
    created_at: datetime


class RedemptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reward_id: uuid.UUID | None
    title_snapshot: str
    cost_snapshot: int
    created_at: datetime
