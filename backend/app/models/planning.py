import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class Plan(Base, TimestampMixin):
    __tablename__ = "plans"
    __table_args__ = (Index("uq_plans_course_version", "course_id", "version", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    goal_summary: Mapped[str] = mapped_column(Text)
    daily_minutes: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date_estimate: Mapped[date | None] = mapped_column(Date)
    projected_basic_date: Mapped[date | None] = mapped_column(Date)
    projected_full_date: Mapped[date | None] = mapped_column(Date)
    rationale_md: Mapped[str] = mapped_column(Text)
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class PlanItem(Base):
    __tablename__ = "plan_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plans.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    scheduled_date: Mapped[date] = mapped_column(Date, index=True)
    type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256))
    description_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    est_minutes: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_minutes: Mapped[int | None] = mapped_column(Integer)


class PlanItemKp(Base):
    __tablename__ = "plan_item_kps"

    plan_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan_items.id", ondelete="CASCADE"), primary_key=True
    )
    kp_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
    )


class CourseBrief(Base, TimestampMixin):
    __tablename__ = "course_briefs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24))
    content_md: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer)


class ResourceSuggestion(Base, TimestampMixin):
    __tablename__ = "resource_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(256))
    why_md: Mapped[str] = mapped_column(Text)
    source_hint: Mapped[str] = mapped_column(String(512), default="", server_default="")
    status: Mapped[str] = mapped_column(
        String(16), default="suggested", server_default="suggested"
    )


class Chat(Base, TimestampMixin):
    __tablename__ = "chats"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    agent: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(128), default="", server_default="")
    context: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )


class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    images: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    scope_class: Mapped[str | None] = mapped_column(String(16))
    matched_kp_ids: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(UUID(as_uuid=True)))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
