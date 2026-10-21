import uuid

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PointsLedger(Base, TimestampMixin):
    __tablename__ = "points_ledger"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "note", name="uq_points_ledger_user_kind_note"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    amount: Mapped[int] = mapped_column(Integer)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0, server_default="1")
    note: Mapped[str] = mapped_column(String(120), default="", server_default="")
    ref_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Reward(Base, TimestampMixin):
    __tablename__ = "rewards"
    __table_args__ = (CheckConstraint("cost > 0", name="ck_rewards_cost_positive"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(500), default="", server_default="")
    cost: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class Redemption(Base, TimestampMixin):
    __tablename__ = "redemptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    reward_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("rewards.id", ondelete="SET NULL")
    )
    title_snapshot: Mapped[str] = mapped_column(String(128))
    cost_snapshot: Mapped[int] = mapped_column(Integer)
