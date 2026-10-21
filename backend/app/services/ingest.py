"""Karpathy Wiki Ingest 管线：解析完成的文档分块整合进课程 Wiki。

任务链：parse_document（解析出 Markdown+图片）→ ingest_document（每课程串行，
把该课程所有已解析未入库文档逐块喂给 LLM 整合），最后重写索引页并记日志。
"""

import logging
import re
import uuid
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier, runtime
from app.jobs.queue import enqueue, update_progress
from app.jobs.worker import job_handler
from app.models import Document, DocumentImage, Job
from app.services import wiki as wiki_svc
from app.services.parsing import parse_file
from app.services.storage import doc_dir

logger = logging.getLogger("learnloop.ingest")

CHUNK_MAX_CHARS = 8000
MAX_PAGES_PER_CHUNK = 4
INGEST_MAX_TOKENS = 16384
# LLM 不得占用的保留 slug（索引/规范/日志由系统维护）
PROTECTED_SLUGS = {"wiki-index", "wiki-schema", "index", "schema", "log", "wiki-log"}

INGEST_CHUNK_SYSTEM_V1 = """你是「学习环」课程知识库的 Wiki 维护师（Ingest v1）。
任务：把用户给出的新资料片段整合进课程 Wiki。

规则：
1. Wiki 页面用 Markdown；页面之间用 [标题](wiki:slug) 互链；slug 用小写英文/数字/连字符（不用中文）
2. 优先增量更新现有页面（整合新信息、保留原有价值内容），仅为重要的新概念创建新页面；单次最多 4 个页面
3. 每页开头一句话概述；正文用小节组织；面向学习者，准确、精炼、成体系；单页正文不超过 800 字
4. 若新资料与现有页面矛盾：用「⚠ 矛盾」小节保留两种说法及来源，不要擅自取舍
5. 数学公式用 $...$ 与 $$...$$；资料中的图片引用 ![..](..) 原样保留到相关页面
6. content_md 是页面完整的新全文（全文替换式）
7. 只输出符合 schema 的 JSON"""

INGEST_FINAL_SYSTEM_V1 = """你是「学习环」课程知识库的 Wiki 维护师（Ingest 收尾 v1）。
一个文档的全部片段已整合完毕。任务：
1. 为该文档写一页「章节总结」（kind=chapter_summary）：文档讲了什么、核心脉络、与哪些概念页相关（用 wiki 链接）
2. 重写整个课程的索引页：按 kind 分组列出全部页面，每页一行 `- [标题](wiki:slug) — 一句话简介`；概念页在前、章节总结在后；不要列出 index/schema 自身
3. 写一条简短的入库日志（一两句话：入库了什么、新建/更新了哪些页面）
只输出符合 schema 的 JSON"""


class PageUpdate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9\-]{1,120}$")
    title: str = Field(max_length=200)
    kind: str = Field(default="concept", pattern=r"^(concept|chapter_summary)$")
    content_md: str
    change_summary: str = Field(default="", max_length=500)


class IngestChunkOut(BaseModel):
    page_updates: list[PageUpdate] = Field(max_length=12)
    log_entry: str = Field(default="", max_length=500)


class IngestFinalOut(BaseModel):
    chapter_summary: PageUpdate
    index_md: str
    log_entry: str = Field(default="", max_length=500)


def chunk_markdown(md: str, max_chars: int = CHUNK_MAX_CHARS) -> list[str]:
    """按标题切分再打包，硬上限兜底。"""
    if len(md) <= max_chars:
        return [md] if md.strip() else []
    sections = re.split(r"(?m)(?=^#{1,3} )", md)
    chunks: list[str] = []
    buf = ""
    for sec in sections:
        while len(sec) > max_chars:
            head, sec = sec[:max_chars], sec[max_chars:]
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(head)
        if len(buf) + len(sec) > max_chars and buf:
            chunks.append(buf)
            buf = sec
        else:
            buf += sec
    if buf.strip():
        chunks.append(buf)
    return chunks


def _pages_brief(pages: list) -> str:
    lines = [
        f"- {p.slug}: {p.title}（{p.kind}）"
        for p in pages
        if p.kind not in ("index", "schema")
    ]
    return "\n".join(lines) if lines else "（尚无内容页）"


@job_handler("parse_document", heavy=True)
async def handle_parse_document(db: AsyncSession, job: Job) -> dict:
    doc_id = uuid.UUID(job.payload["document_id"])
    doc = await db.get(Document, doc_id)
    if doc is None:
        return {"skipped": "文档不存在"}
    doc.status = "parsing"
    await db.commit()

    try:
        ext = Path(doc.original_name).suffix.lower()
        result = await parse_file(
            Path(doc.stored_path),
            ext,
            doc.course_id,
            doc.id,
            doc.original_name,
        )
        parsed_path = doc_dir(doc.course_id, doc.id) / "parsed.md"
        parsed_path.write_text(result.markdown, encoding="utf-8")
        doc.parsed_md_path = str(parsed_path)
        doc.page_count = result.page_count
        doc.status = "parsed"
        doc.error = None
        for img in result.image_paths:
            db.add(DocumentImage(course_id=doc.course_id, document_id=doc.id, stored_path=str(img)))
        await db.commit()
    except Exception as e:
        doc.status = "failed"
        doc.error = f"解析失败: {type(e).__name__}: {e}"[:2000]
        await db.commit()
        raise

    await enqueue(
        db, "ingest_document", {"course_id": str(doc.course_id)},
        course_id=doc.course_id, dedupe_key=f"ingest:{doc.course_id}",
    )
    await db.commit()
    job_result = {"chars": len(result.markdown), "images": len(result.image_paths)}
    job_result.update(result.details)
    return job_result


@job_handler("ingest_document", heavy=True)
async def handle_ingest_document(db: AsyncSession, job: Job) -> dict:
    """处理该课程全部 parsed 状态文档（每课程同一时间只有一个该任务，天然串行）。"""
    course_id = uuid.UUID(job.payload["course_id"])
    ingested: list[str] = []

    while True:
        # 含 ingesting：进程崩溃遗留的文档由重排后的本任务接手重新入库
        doc = (
            await db.execute(
                select(Document)
                .where(Document.course_id == course_id, Document.status.in_(("parsed", "ingesting")))
                .order_by(Document.created_at)
                .limit(1)
            )
        ).scalar_one_or_none()
        if doc is None:
            break
        await _ingest_one(db, job, doc)
        ingested.append(doc.original_name)

    return {"ingested": ingested}


async def _ingest_one(db: AsyncSession, job: Job, doc: Document) -> None:
    resume_from = doc.ingest_chunk_done or 0
    doc.status = "ingesting"
    await db.commit()

    try:
        md = Path(doc.parsed_md_path).read_text(encoding="utf-8")
        chunks = chunk_markdown(md)
        resume_from = min(resume_from, len(chunks))
        if resume_from:
            logger.info("续入库 %s：从第 %d/%d 段继续", doc.original_name, resume_from + 1, len(chunks))
        schema_page = await wiki_svc.get_page(db, doc.course_id, wiki_svc.SCHEMA_SLUG)
        pages = await wiki_svc.list_pages(db, doc.course_id)
        stable_blocks = [
            f"# Wiki 维护规范\n{schema_page.content_md if schema_page else ''}",
            f"# 课程现有页面清单\n{_pages_brief(pages)}",
        ]
        touched: dict[str, str] = {}

        for i, chunk in enumerate(chunks):
            if i < resume_from:
                continue
            await update_progress(
                db, job.id,
                {"document": doc.original_name, "chunk": i + 1, "total": len(chunks)},
            )
            touched_brief = (
                "\n".join(f"- {s}: {t}" for s, t in touched.items()) if touched else "（无）"
            )
            volatile = (
                f"# 本次资料\n文档：{doc.original_name}（第 {i + 1}/{len(chunks)} 段）\n\n"
                f"# 本文档此前片段已建/改页面\n{touched_brief}\n\n"
                f"# 资料片段\n{chunk}\n\n"
                f"请把这段资料整合进 Wiki，输出 JSON。"
            )
            out: IngestChunkOut = await runtime.run_structured(
                agent="wiki", operation="ingest_chunk", tier=Tier.medium,
                response_model=IngestChunkOut,
                system_prompt=INGEST_CHUNK_SYSTEM_V1, stable_blocks=stable_blocks,
                volatile=volatile, course_id=doc.course_id, job_id=job.id,
                max_tokens=INGEST_MAX_TOKENS, temperature=0.3,
            )
            for pu in out.page_updates[:MAX_PAGES_PER_CHUNK]:
                if pu.slug in PROTECTED_SLUGS:
                    continue
                await wiki_svc.upsert_page(
                    db, doc.course_id, slug=pu.slug, title=pu.title, kind=pu.kind,
                    content_md=pu.content_md, change_summary=pu.change_summary or f"入库: {doc.original_name}",
                    updated_by="ingest", job_id=job.id,
                    source_ref={"document_id": str(doc.id), "locator": f"片段 {i + 1}/{len(chunks)}"},
                )
                touched[pu.slug] = pu.title
            doc.ingest_chunk_done = i + 1
            await db.commit()

        # 收尾：章节总结 + 重写索引 + 日志
        pages = await wiki_svc.list_pages(db, doc.course_id)
        final_volatile = (
            f"# 文档\n{doc.original_name}\n\n"
            f"# 本文档触及页面\n{chr(10).join(f'- {s}: {t}' for s, t in touched.items()) or '（无）'}\n\n"
            f"# 课程全部页面\n{_pages_brief(pages)}\n\n"
            f"# 旧索引页\n{(await wiki_svc.get_page(db, doc.course_id, wiki_svc.INDEX_SLUG)).content_md}\n\n"
            f"请输出 JSON。"
        )
        final: IngestFinalOut = await runtime.run_structured(
            agent="wiki", operation="ingest_finalize", tier=Tier.medium,
            response_model=IngestFinalOut,
            system_prompt=INGEST_FINAL_SYSTEM_V1, stable_blocks=None,
            volatile=final_volatile, course_id=doc.course_id, job_id=job.id,
            max_tokens=INGEST_MAX_TOKENS, temperature=0.3,
        )
        cs = final.chapter_summary
        await wiki_svc.upsert_page(
            db, doc.course_id, slug=cs.slug, title=cs.title, kind="chapter_summary",
            content_md=cs.content_md, change_summary=f"文档总结: {doc.original_name}",
            updated_by="ingest", job_id=job.id,
            source_ref={"document_id": str(doc.id), "locator": "全文"},
        )
        await wiki_svc.upsert_page(
            db, doc.course_id, slug=wiki_svc.INDEX_SLUG, title="课程知识索引", kind="index",
            content_md=final.index_md, change_summary=f"入库重建索引: {doc.original_name}",
            updated_by="ingest", job_id=job.id,
        )
        await wiki_svc.append_log(
            db, doc.course_id, "ingest",
            final.log_entry or f"入库文档《{doc.original_name}》，更新 {len(touched)} 个页面",
            job_id=job.id,
        )
        doc.status = "ingested"
        doc.ingest_chunk_done = 0
        doc.error = None
        await db.commit()
        await enqueue(
            db,
            "extract_kps",
            {"course_id": str(doc.course_id)},
            course_id=doc.course_id,
            dedupe_key=f"extract_kps:{doc.course_id}",
        )
        await db.commit()
    except Exception as e:
        await db.rollback()
        doc.error = f"入库失败: {type(e).__name__}: {e}"[:2000]
        # 未耗尽重试次数则保持 ingesting，重试时从 ingest_chunk_done 续跑
        if job.attempts >= job.max_attempts:
            doc.status = "failed"
        await db.commit()
        raise
