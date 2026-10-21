import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class VocabEntry(Base, TimestampMixin):
    __tablename__ = "vocab_entries"
    __table_args__ = (
        UniqueConstraint("course_id", "word", name="uq_vocab_entries_course_word"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    word: Mapped[str] = mapped_column(String(64))
    phonetic: Mapped[str] = mapped_column(String(64), default="", server_default="")
    meaning_md: Mapped[str] = mapped_column(Text)
    example_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_note: Mapped[str] = mapped_column(String(120), default="", server_default="")


class ReviewCard(Base, TimestampMixin):
    __tablename__ = "review_cards"
    __table_args__ = (
        CheckConstraint(
            "(kp_id IS NULL) != (vocab_id IS NULL)",
            name="ck_review_cards_exactly_one_source",
        ),
        UniqueConstraint("vocab_id", name="uq_review_cards_vocab_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    kp_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), unique=True
    )
    vocab_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vocab_entries.id", ondelete="CASCADE")
    )
    stability: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    difficulty: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    due: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    state: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    reps: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    lapses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_review: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fsrs_card: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


class ReviewLog(Base, TimestampMixin):
    __tablename__ = "review_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    card_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("review_cards.id", ondelete="SET NULL"), index=True
    )
    kp_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE")
    )
    vocab_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vocab_entries.id", ondelete="SET NULL")
    )
    rating: Mapped[int] = mapped_column(SmallInteger)
    state_before: Mapped[dict] = mapped_column(JSONB)
    state_after: Mapped[dict] = mapped_column(JSONB)


class TangentItem(Base, TimestampMixin):
    __tablename__ = "tangent_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    chat_message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chat_messages.id", ondelete="SET NULL")
    )
    question: Mapped[str] = mapped_column(Text)
    brief_answer: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), default="parked", server_default="parked")


class StudySession(Base):
    __tablename__ = "study_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    last_beat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=text("now()")
    )
    seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class UserDailyStat(Base):
    __tablename__ = "user_daily_stats"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    study_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    review_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    learn_items_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
