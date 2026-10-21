import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Course, WikiLogEntry, WikiPage, WikiPageRevision, utcnow

WIKI_LINK_RE = re.compile(r"\[([^\]]+)\]\(wiki:([a-z0-9\-]+)\)")

SCHEMA_SLUG = "wiki-schema"
INDEX_SLUG = "wiki-index"

SCHEMA_TEMPLATE = """# Wiki 维护规范 v1

本课程 Wiki 由 AI 依据以下约定自动维护，人工修改本页可调整 AI 行为。

## 页面
- 页面为 Markdown；页面间用 `[标题](wiki:slug)` 互链，slug 用小写英文/数字/连字符
- 每页开头一句话概述主题，然后分小节展开
- kind 分类：concept=概念页，chapter_summary=章节/文档总结，index=索引，schema=本规范

## 入库（Ingest）
- 新资料按片段整合：优先增量更新现有页面，仅为重要新概念建新页
- 与已有内容矛盾时，用「⚠ 矛盾」标注并保留两种说法及来源，等待人工裁决
- 数学公式用 $...$ 与 $$...$$；资料中的图片引用原样保留
- 每个文档入库完成后生成一页章节总结，并重写索引页

## 索引页
- 目录式：按 kind 分组列出全部页面，每页一行 `- [标题](wiki:slug) — 一句话简介`

## 日志
- 每次入库/回填/体检追加一条日志（append-only），供人回溯 Wiki 演变
"""

INDEX_TEMPLATE = """# 课程知识索引

（暂无内容——导入资料并入库后自动生成）
"""


def extract_out_links(md: str) -> list[str]:
    return sorted({m.group(2) for m in WIKI_LINK_RE.finditer(md)})


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9\-]+", "-", text.lower()).strip("-")
    return s[:120] or f"page-{uuid.uuid4().hex[:6]}"


async def seed_course_wiki(db: AsyncSession, course: Course) -> None:
    db.add(
        WikiPage(
            course_id=course.id, slug=SCHEMA_SLUG, title="Wiki 维护规范", kind="schema",
            content_md=SCHEMA_TEMPLATE, updated_by="user",
        )
    )
    db.add(
        WikiPage(
            course_id=course.id, slug=INDEX_SLUG, title="课程知识索引", kind="index",
            content_md=INDEX_TEMPLATE, updated_by="user",
        )
    )


async def get_page(db: AsyncSession, course_id: uuid.UUID, slug: str) -> WikiPage | None:
    return (
        await db.execute(
            select(WikiPage).where(WikiPage.course_id == course_id, WikiPage.slug == slug)
        )
    ).scalar_one_or_none()


async def list_pages(db: AsyncSession, course_id: uuid.UUID) -> list[WikiPage]:
    return list(
        (
            await db.execute(
                select(WikiPage).where(WikiPage.course_id == course_id).order_by(WikiPage.kind, WikiPage.title)
            )
        ).scalars()
    )


async def upsert_page(
    db: AsyncSession,
    course_id: uuid.UUID,
    *,
    slug: str,
    title: str,
    kind: str,
    content_md: str,
    change_summary: str,
    updated_by: str,
    job_id: uuid.UUID | None = None,
    source_ref: dict | None = None,
) -> WikiPage:
    page = await get_page(db, course_id, slug)
    if page is None:
        page = WikiPage(
            course_id=course_id, slug=slug, title=title, kind=kind,
            content_md=content_md, out_links=extract_out_links(content_md),
            source_refs=[source_ref] if source_ref else [], updated_by=updated_by,
        )
        db.add(page)
        await db.flush()
    else:
        page.version += 1
        page.title = title
        page.kind = kind if page.kind not in ("index", "schema") else page.kind
        page.content_md = content_md
        page.out_links = extract_out_links(content_md)
        page.updated_by = updated_by
        page.updated_at = utcnow()
        if source_ref:
            refs = list(page.source_refs or [])
            if source_ref not in refs:
                refs.append(source_ref)
            page.source_refs = refs
    db.add(
        WikiPageRevision(
            page_id=page.id, version=page.version, content_md=content_md,
            change_summary=change_summary, job_id=job_id,
        )
    )
    return page


async def append_log(
    db: AsyncSession, course_id: uuid.UUID, op: str, entry_md: str, job_id: uuid.UUID | None = None
) -> None:
    db.add(WikiLogEntry(course_id=course_id, op=op, entry_md=entry_md, job_id=job_id))
