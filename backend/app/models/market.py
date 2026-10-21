import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class MarketCourse(Base, TimestampMixin):
    __tablename__ = "market_courses"
    __table_args__ = (
        CheckConstraint("price_points >= 0", name="ck_market_courses_price_nonnegative"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_market_courses_status",
        ),
        CheckConstraint("current_version >= 0", name="ck_market_courses_version_nonnegative"),
        UniqueConstraint("source_course_id", name="uq_market_courses_source_course"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    price_points: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(String(16), default="draft", server_default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


class MarketCourseVersion(Base, TimestampMixin):
    __tablename__ = "market_course_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('building', 'ready', 'failed')",
            name="ck_market_course_versions_status",
        ),
        CheckConstraint("version > 0", name="ck_market_course_versions_version_positive"),
        UniqueConstraint(
            "market_course_id", "version", name="uq_market_course_versions_course_version"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    market_course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_courses.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="building", server_default="building")
    manifest: Mapped[dict] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    package_path: Mapped[str] = mapped_column(String(512), default="", server_default="")
    package_sha256: Mapped[str] = mapped_column(String(64), default="", server_default="")
    error: Mapped[str | None] = mapped_column(Text)
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


class MarketAcquisition(Base, TimestampMixin):
    __tablename__ = "market_acquisitions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_market_acquisitions_status",
        ),
        CheckConstraint("price_snapshot >= 0", name="ck_market_acquisitions_price_nonnegative"),
        CheckConstraint("version_snapshot > 0", name="ck_market_acquisitions_version_positive"),
        UniqueConstraint("user_id", "market_course_id", name="uq_market_acquisitions_user_course"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    market_course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_courses.id", ondelete="RESTRICT"), index=True
    )
    market_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("market_course_versions.id", ondelete="SET NULL"), index=True
    )
    target_course_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("courses.id", ondelete="SET NULL"), index=True
    )
    title_snapshot: Mapped[str] = mapped_column(String(128))
    price_snapshot: Mapped[int] = mapped_column(Integer)
    version_snapshot: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )
