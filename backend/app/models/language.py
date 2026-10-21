import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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

LEVEL_CHECK = "value IS NULL OR value IN ('A1', 'A2', 'B1', 'B2', 'C1')"


class LanguageProfile(Base):
    __tablename__ = "language_profiles"
    __table_args__ = (
        CheckConstraint("target_language = 'en'", name="ck_language_profiles_target_en"),
        CheckConstraint(
            "assessment_status IN ('required', 'building', 'ready', 'active', 'stale')",
            name="ck_language_profiles_assessment_status",
        ),
        CheckConstraint(
            "intensity_mode IN ('adaptive', 'manual')",
            name="ck_language_profiles_intensity_mode",
        ),
        CheckConstraint(
            LEVEL_CHECK.replace("value", "overall_level"),
            name="ck_language_profiles_overall_level",
        ),
        CheckConstraint(
            LEVEL_CHECK.replace("value", "reading_level"),
            name="ck_language_profiles_reading_level",
        ),
        CheckConstraint(
            LEVEL_CHECK.replace("value", "writing_level"),
            name="ck_language_profiles_writing_level",
        ),
        CheckConstraint(
            "vocab_total >= 0 AND vocab_known_estimate >= 0 AND "
            "vocab_ci_low >= 0 AND vocab_ci_high >= 0",
            name="ck_language_profiles_vocab_counts",
        ),
        CheckConstraint(
            "recommended_daily_words BETWEEN 5 AND 100",
            name="ck_language_profiles_daily_words",
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True
    )
    target_language: Mapped[str] = mapped_column(String(16), default="en", server_default="en")
    assessment_status: Mapped[str] = mapped_column(
        String(16), default="required", server_default="required"
    )
    overall_level: Mapped[str | None] = mapped_column(String(2))
    reading_level: Mapped[str | None] = mapped_column(String(2))
    writing_level: Mapped[str | None] = mapped_column(String(2))
    vocab_total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vocab_known_estimate: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vocab_ci_low: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    vocab_ci_high: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reading_score: Mapped[int | None] = mapped_column(SmallInteger)
    writing_score: Mapped[int | None] = mapped_column(SmallInteger)
    intensity_mode: Mapped[str] = mapped_column(
        String(16), default="adaptive", server_default="adaptive"
    )
    recommended_daily_words: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    intensive_target_words: Mapped[int] = mapped_column(Integer, default=400, server_default="400")
    extensive_target_words: Mapped[int] = mapped_column(Integer, default=800, server_default="800")
    writing_target_words: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    latest_assessment_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


class LanguageSource(Base, TimestampMixin):
    __tablename__ = "language_sources"
    __table_args__ = (
        CheckConstraint("kind IN ('vocab', 'reading')", name="ck_language_sources_kind"),
        CheckConstraint(
            "status IN ('uploaded', 'processing', 'ready', 'failed')",
            name="ck_language_sources_status",
        ),
        CheckConstraint(
            LEVEL_CHECK.replace("value", "estimated_level"),
            name="ck_language_sources_estimated_level",
        ),
        UniqueConstraint("course_id", "kind", "sha256", name="uq_language_sources_course_kind_sha"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    original_name: Mapped[str] = mapped_column(String(256))
    stored_path: Mapped[str] = mapped_column(String(512), default="", server_default="")
    mime: Mapped[str] = mapped_column(String(128), default="", server_default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="uploaded", server_default="uploaded")
    parsed_md_path: Mapped[str | None] = mapped_column(String(512))
    word_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimated_level: Mapped[str | None] = mapped_column(String(2))
    analysis: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


class VocabSourceEntry(Base):
    __tablename__ = "vocab_source_entries"

    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("language_sources.id", ondelete="CASCADE"), primary_key=True
    )
    vocab_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vocab_entries.id", ondelete="CASCADE"), primary_key=True
    )
    source_rank: Mapped[int | None] = mapped_column(Integer)
    level_hint: Mapped[str] = mapped_column(String(32), default="", server_default="")


class LanguageAssessment(Base, TimestampMixin):
    __tablename__ = "language_assessments"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('baseline', 'source_check', 'periodic')",
            name="ck_language_assessments_kind",
        ),
        CheckConstraint(
            "status IN ('building', 'ready', 'submitted', 'graded', 'failed')",
            name="ck_language_assessments_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="building", server_default="building")
    source_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    item_bundle: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    response_bundle: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    result: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


class ReadingExercise(Base, TimestampMixin):
    __tablename__ = "reading_exercises"
    __table_args__ = (
        CheckConstraint("mode IN ('intensive', 'extensive')", name="ck_reading_exercises_mode"),
        CheckConstraint(
            "status IN ('open', 'submitted', 'graded')", name="ck_reading_exercises_status"
        ),
        CheckConstraint(
            LEVEL_CHECK.replace("value", "target_level"),
            name="ck_reading_exercises_target_level",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("language_sources.id", ondelete="CASCADE"), index=True
    )
    plan_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_items.id", ondelete="SET NULL"), index=True
    )
    mode: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(256))
    passage_md: Mapped[str] = mapped_column(Text)
    target_level: Mapped[str | None] = mapped_column(String(2))
    questions: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    focus_vocab: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")


class ReadingAttempt(Base, TimestampMixin):
    __tablename__ = "reading_attempts"
    __table_args__ = (
        CheckConstraint("attempt_no > 0", name="ck_reading_attempts_attempt_no"),
        CheckConstraint("duration_seconds BETWEEN 1 AND 14400", name="ck_reading_attempts_duration"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_reading_attempts_score"),
        UniqueConstraint("exercise_id", "attempt_no", name="uq_reading_attempts_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reading_exercises.id", ondelete="CASCADE"), index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    answers: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    response_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    duration_seconds: Mapped[int] = mapped_column(Integer)
    main_idea: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    detail: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    inference: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    context_vocab: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    summary: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    score: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    feedback_md: Mapped[str] = mapped_column(Text, default="", server_default="")


class WritingExercise(Base, TimestampMixin):
    __tablename__ = "writing_exercises"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'grading', 'revision_due', 'completed')",
            name="ck_writing_exercises_status",
        ),
        CheckConstraint(
            LEVEL_CHECK.replace("value", "target_level"),
            name="ck_writing_exercises_target_level",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    source_reading_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reading_exercises.id", ondelete="SET NULL"), index=True
    )
    plan_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan_items.id", ondelete="SET NULL"), index=True
    )
    prompt_md: Mapped[str] = mapped_column(Text)
    requirements_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    target_level: Mapped[str | None] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WritingSubmission(Base, TimestampMixin):
    __tablename__ = "writing_submissions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_writing_submissions_version"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_writing_submissions_score"),
        UniqueConstraint("exercise_id", "version", name="uq_writing_submissions_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exercise_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("writing_exercises.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    submission_md: Mapped[str] = mapped_column(Text)
    task_fulfillment: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    organization: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    grammar: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    vocabulary: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    score: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    feedback_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
