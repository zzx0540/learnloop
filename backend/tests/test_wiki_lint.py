import uuid

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier
from app.models import (
    Course,
    Job,
    User,
    WikiLintFinding,
    WikiLogEntry,
    WikiPage,
    WikiPageRevision,
)
from app.services import wiki_lint as lint_svc
from app.services.wiki_lint import LintFlagOut, WikiLintOut


async def test_wiki_lint_repairs_flags_dedupes_and_enforces_ownership(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course_response = await client.post(
        "/api/v1/courses",
        json={"name": "Wiki 体检课", "goal_text": "验证体检", "daily_minutes": 30},
    )
    assert course_response.status_code == 200
    course_id = uuid.UUID(course_response.json()["id"])
    index = (
        await db.execute(
            select(WikiPage).where(
                WikiPage.course_id == course_id,
                WikiPage.slug == "wiki-index",
            )
        )
    ).scalar_one()
    index.content_md = "# 索引\n\n- [来源页](wiki:source-page)"
    source = WikiPage(
        course_id=course_id,
        slug="source-page",
        title="来源页",
        kind="chapter_summary",
        content_md=(
            "# 来源页\n\n"
            "参见 [主动回忆](wiki:ActiveRecall)，以及 [缺失页面](wiki:missing-page)。"
        ),
    )
    target = WikiPage(
        course_id=course_id,
        slug="active-recall",
        title="主动回忆",
        kind="concept",
        content_md="# 主动回忆\n\n主动回忆通过提取练习强化记忆。",
    )
    orphan = WikiPage(
        course_id=course_id,
        slug="orphan-concept",
        title="孤儿概念",
        kind="concept",
        content_md="# 孤儿概念\n\n这个页面暂时没有链接。",
    )
    db.add_all([source, target, orphan])
    await db.flush()
    existing = WikiLintFinding(
        course_id=course_id,
        page_id=target.id,
        kind="contradiction",
        detail_md="旧描述",
    )
    job = Job(
        kind="wiki_lint",
        payload={"course_id": str(course_id)},
        course_id=course_id,
        user_id=logged_in.id,
    )
    db.add_all([existing, job])
    await db.commit()
    source_id = source.id
    existing_id = existing.id
    captured = {}

    async def fake_structured(**kwargs):
        captured.update(kwargs)
        return WikiLintOut(
            findings=[
                LintFlagOut(
                    page_slug="active-recall",
                    kind="contradiction",
                    detail="索引摘要与页面首段对定义的表述不一致。",
                ),
                LintFlagOut(
                    page_slug="not-in-course",
                    kind="outdated",
                    detail="未知页面必须被过滤。",
                ),
            ]
        )

    monkeypatch.setattr(lint_svc.runtime, "run_structured", fake_structured)
    result = await lint_svc.handle_wiki_lint(db, job)
    assert result == {
        "fixed_links": 1,
        "broken_findings": 1,
        "orphan_findings": 1,
        "llm_findings": 1,
    }
    assert captured["tier"] is Tier.low
    assert captured["thinking"] is False

    db.expire_all()
    repaired = await db.get(WikiPage, source_id)
    assert "wiki:active-recall" in repaired.content_md
    assert "wiki:missing-page" in repaired.content_md
    assert repaired.version == 2
    assert repaired.updated_by == "lint"
    revision = (
        await db.execute(
            select(WikiPageRevision).where(WikiPageRevision.page_id == source_id)
        )
    ).scalar_one()
    assert revision.version == 2
    assert revision.change_summary == "Wiki lint 自动修复近似死链"
    assert (
        await db.execute(
            select(func.count()).select_from(WikiLogEntry).where(
                WikiLogEntry.course_id == course_id,
                WikiLogEntry.op == "lint",
            )
        )
    ).scalar_one() == 2

    findings = list(
        (
            await db.execute(
                select(WikiLintFinding)
                .where(WikiLintFinding.course_id == course_id)
                .order_by(WikiLintFinding.kind)
            )
        ).scalars()
    )
    assert [(finding.kind, finding.status) for finding in findings] == [
        ("broken_link", "open"),
        ("contradiction", "open"),
        ("orphan", "open"),
    ]
    contradiction = next(finding for finding in findings if finding.kind == "contradiction")
    assert contradiction.id == existing_id
    assert contradiction.detail_md == "索引摘要与页面首段对定义的表述不一致。"

    listed = await client.get(
        f"/api/v1/courses/{course_id}/wiki/lint-findings?status=open"
    )
    assert listed.status_code == 200 and len(listed.json()) == 3
    broken = next(item for item in listed.json() if item["kind"] == "broken_link")
    resolved = await client.patch(
        f"/api/v1/wiki/lint-findings/{broken['id']}", json={"status": "resolved"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["status"] == "resolved"

    outsider = User(
        username="lint-outsider",
        password_hash="not-used",
        nickname="其他用户",
    )
    db.add(outsider)
    await db.flush()
    outsider_course = Course(user_id=outsider.id, name="他人 Wiki")
    db.add(outsider_course)
    await db.flush()
    outsider_finding = WikiLintFinding(
        course_id=outsider_course.id,
        kind="orphan",
        detail_md="他人记录",
    )
    db.add(outsider_finding)
    await db.commit()
    forbidden = await client.patch(
        f"/api/v1/wiki/lint-findings/{outsider_finding.id}",
        json={"status": "dismissed"},
    )
    assert forbidden.status_code == 404
