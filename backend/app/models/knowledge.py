import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class KnowledgePoint(Base, TimestampMixin):
    __tablename__ = "knowledge_points"
    __table_args__ = (
        Index("uq_knowledge_points_course_slug", "course_id", "slug", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    slug: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(256))
    summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    recall_prompt: Mapped[str] = mapped_column(String(200), default="", server_default="")
    primary_wiki_page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wiki_pages.id", ondelete="SET NULL")
    )
    importance: Mapped[int] = mapped_column(SmallInteger, default=3, server_default="3")
    difficulty: Mapped[int] = mapped_column(SmallInteger, default=3, server_default="3")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    mastery_score: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
    first_learned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    basic_mastered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fully_mastered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    projected_basic_at: Mapped[date | None] = mapped_column(Date)
    projected_full_at: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[str] = mapped_column(String(16), default="planner", server_default="planner")


class KpEdge(Base):
    __tablename__ = "kp_edges"
    __table_args__ = (
        Index("uq_kp_edges_src_dst_relation", "src_kp_id", "dst_kp_id", "relation", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), index=True
    )
    src_kp_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE")
    )
    dst_kp_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE")
    )
    relation: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(16), default="planner", server_default="planner")


class KpWikiLink(Base):
    __tablename__ = "kp_wiki_links"

    kp_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge_points.id", ondelete="CASCADE"), primary_key=True
    )
    wiki_page_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wiki_pages.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16), default="mention", server_default="mention")
