import json
import re
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier, runtime
from app.jobs.worker import job_handler
from app.models import Course, Job, WikiLintFinding, WikiPage
from app.services.wiki import INDEX_SLUG, append_log, upsert_page

WIKI_LINT_SYSTEM_V1 = """# Wiki 内容体检员 v1

根据课程 Wiki 索引和各页首段摘要，只标记内容之间明确的矛盾或可能过时的信息。
不要检查链接和孤儿页，不要改写正文。detail 说明具体依据，不超过 200 字。只输出 JSON。"""

WIKI_LINT_LINK_RE = re.compile(r"\[([^\]]+)\]\(wiki:([A-Za-z0-9-]+)\)")
CODE_SPAN_RE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


class LintFlagOut(BaseModel):
    page_slug: str
    kind: Literal["contradiction", "outdated"]
    detail: str = Field(min_length=1, max_length=200)


class WikiLintOut(BaseModel):
    findings: list[LintFlagOut]


@dataclass(frozen=True)
class WikiLink:
    target: str
    start: int
    end: int


def _wiki_links(content_md: str) -> list[WikiLink]:
    masked = list(content_md)
    for span in CODE_SPAN_RE.finditer(content_md):
        masked[span.start() : span.end()] = " " * (span.end() - span.start())
    visible = "".join(masked)
    return [
        WikiLink(target=match.group(2), start=match.start(2), end=match.end(2))
        for match in WIKI_LINT_LINK_RE.finditer(visible)
    ]


def _near_slug(target: str, slugs: set[str]) -> str | None:
    normalized = target.lower().replace("-", "")
    candidates = [slug for slug in slugs if slug.lower().replace("-", "") == normalized]
    return candidates[0] if len(candidates) == 1 else None


def _first_paragraph(content_md: str) -> str:
    paragraphs = re.split(r"\n\s*\n", content_md.strip())
    for paragraph in paragraphs:
        text = " ".join(line.strip() for line in paragraph.splitlines() if not line.startswith("#"))
        if text:
            return text[:500]
    return "（无摘要）"


async def _upsert_finding(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    page: WikiPage,
    kind: str,
    detail: str,
) -> WikiLintFinding:
    finding = (
        await db.execute(
            select(WikiLintFinding).where(
                WikiLintFinding.course_id == course_id,
                WikiLintFinding.page_id == page.id,
                WikiLintFinding.kind == kind,
                WikiLintFinding.status == "open",
            )
        )
    ).scalar_one_or_none()
    if finding is None:
        finding = WikiLintFinding(
            course_id=course_id,
            page_id=page.id,
            kind=kind,
            detail_md=detail,
        )
        db.add(finding)
    else:
        finding.detail_md = detail
    return finding


@job_handler("wiki_lint")
async def handle_wiki_lint(db: AsyncSession, job: Job) -> dict:
    course_id = uuid.UUID(job.payload["course_id"])
    course = await db.get(Course, course_id)
    if course is None:
        return {"skipped": "课程不存在"}
    pages = list(
        (
            await db.execute(
                select(WikiPage)
                .where(WikiPage.course_id == course_id)
                .order_by(WikiPage.slug)
            )
        ).scalars()
    )
    page_by_slug = {page.slug: page for page in pages}
    valid_slugs = set(page_by_slug)
    fixed_links = 0
    broken_findings = 0

    for page in pages:
        replacements: list[tuple[int, int, str]] = []
        unresolved: set[str] = set()
        for link in _wiki_links(page.content_md):
            if link.target in valid_slugs:
                continue
            near = _near_slug(link.target, valid_slugs)
            if near is None:
                unresolved.add(link.target)
            else:
                replacements.append((link.start, link.end, near))
        if replacements:
            content_md = page.content_md
            for start, end, target in reversed(replacements):
                content_md = content_md[:start] + target + content_md[end:]
            await upsert_page(
                db,
                course_id,
                slug=page.slug,
                title=page.title,
                kind=page.kind,
                content_md=content_md,
                change_summary="Wiki lint 自动修复近似死链",
                updated_by="lint",
                job_id=job.id,
            )
            fixed_links += len(replacements)
            await append_log(
                db,
                course_id,
                "lint",
                f"自动修复页面 [{page.title}](wiki:{page.slug}) 的 {len(replacements)} 个近似死链",
                job_id=job.id,
            )
        if unresolved:
            await _upsert_finding(
                db,
                course_id=course_id,
                page=page,
                kind="broken_link",
                detail="失效链接：" + "、".join(f"wiki:{slug}" for slug in sorted(unresolved)),
            )
            broken_findings += 1

    incoming: set[str] = set()
    for page in pages:
        incoming.update(
            link.target for link in _wiki_links(page.content_md) if link.target in valid_slugs
        )
    index_page = page_by_slug.get(INDEX_SLUG)
    index_links = {
        link.target for link in _wiki_links(index_page.content_md)
    } if index_page else set()
    orphan_findings = 0
    for page in pages:
        if page.kind == "concept" and page.slug not in incoming and page.slug not in index_links:
            await _upsert_finding(
                db,
                course_id=course_id,
                page=page,
                kind="orphan",
                detail="该概念页没有任何入链，且未列入课程索引。",
            )
            orphan_findings += 1

    summaries = [
        {"page_slug": page.slug, "title": page.title, "summary": _first_paragraph(page.content_md)}
        for page in pages
    ]
    lint_output: WikiLintOut = await runtime.run_structured(
        agent="wiki_lint",
        operation="wiki_lint",
        tier=Tier.low,
        response_model=WikiLintOut,
        system_prompt=WIKI_LINT_SYSTEM_V1,
        stable_blocks=[
            f"# Wiki 索引\n{index_page.content_md if index_page else '（暂无）'}"
        ],
        volatile=json.dumps(summaries, ensure_ascii=False),
        course_id=course_id,
        job_id=job.id,
        max_tokens=4096,
        temperature=0.1,
        thinking=False,
    )
    llm_findings = 0
    seen_flags: set[tuple[str, str]] = set()
    for item in lint_output.findings:
        page = page_by_slug.get(item.page_slug)
        key = (item.page_slug, item.kind)
        if page is None or key in seen_flags:
            continue
        seen_flags.add(key)
        await _upsert_finding(
            db,
            course_id=course_id,
            page=page,
            kind=item.kind,
            detail=item.detail,
        )
        llm_findings += 1
    await append_log(
        db,
        course_id,
        "lint",
        (
            f"Wiki 体检完成：自动修复 {fixed_links} 个链接，"
            f"死链 {broken_findings} 页，孤儿页 {orphan_findings}，内容标记 {llm_findings} 条"
        ),
        job_id=job.id,
    )
    await db.commit()
    return {
        "fixed_links": fixed_links,
        "broken_findings": broken_findings,
        "orphan_findings": orphan_findings,
        "llm_findings": llm_findings,
    }
