import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class WikiPage(Base, TimestampMixin):
    __tablename__ = "wiki_pages"
    __table_args__ = (Index("uq_wiki_pages_course_slug", "course_id", "slug", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(24), default="concept", server_default="concept")
    content_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    out_links: Mapped[list[str]] = mapped_column(
        ARRAY(String(128)), default=list, server_default=text("'{}'::varchar[]")
    )
    source_refs: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    updated_by: Mapped[str] = mapped_column(String(24), default="ingest", server_default="ingest")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=text("now()")
    )


class WikiPageRevision(Base, TimestampMixin):
    __tablename__ = "wiki_page_revisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("wiki_pages.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content_md: Mapped[str] = mapped_column(Text)
    change_summary: Mapped[str] = mapped_column(Text, default="", server_default="")
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class WikiLogEntry(Base, TimestampMixin):
    __tablename__ = "wiki_log_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    op: Mapped[str] = mapped_column(String(24))
    entry_md: Mapped[str] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class WikiLintFinding(Base, TimestampMixin):
    __tablename__ = "wiki_lint_findings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("wiki_pages.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(24))
    detail_md: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
