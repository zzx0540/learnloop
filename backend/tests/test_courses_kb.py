import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import runtime as runtime_module
from app.models import Course, Document, InviteCode, Job, User, WikiLogEntry, WikiPage
from app.services import ingest as ingest_svc
from app.services.ingest import IngestChunkOut, IngestFinalOut, PageUpdate
from app.services.storage import course_dir


async def _create_course(client: AsyncClient) -> dict:
    r = await client.post("/api/v1/courses", json={"name": "概率论", "goal_text": "掌握贝叶斯"})
    assert r.status_code == 200, r.text
    return r.json()


async def test_course_crud_and_wiki_seed(client: AsyncClient, db: AsyncSession, logged_in: User) -> None:
    course = await _create_course(client)

    pages = (
        await db.execute(select(WikiPage).where(WikiPage.course_id == uuid.UUID(course["id"])))
    ).scalars().all()
    kinds = {p.kind for p in pages}
    assert kinds == {"schema", "index"}  # 建课自动播种

    r = await client.get("/api/v1/courses")
    assert len(r.json()) == 1
    r = await client.patch(f"/api/v1/courses/{course['id']}", json={"daily_minutes": 60})
    assert r.json()["daily_minutes"] == 60
    directory = course_dir(uuid.UUID(course["id"]))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "owned.txt").write_text("可删除", encoding="utf-8")
    r = await client.delete(f"/api/v1/courses/{course['id']}")
    assert r.json()["ok"] is True
    assert not directory.exists()


async def test_course_ownership_isolation(client: AsyncClient, db: AsyncSession, logged_in: User) -> None:
    course = await _create_course(client)

    db.add(InviteCode(code="TEST2", max_uses=5))
    await db.commit()
    r = await client.post(
        "/api/v1/auth/register",
        json={"username": "student2", "password": "password123", "nickname": "小红", "invite_code": "TEST2"},
    )
    assert r.status_code == 200
    r = await client.get(f"/api/v1/courses/{course['id']}")
    assert r.status_code == 404  # 他人课程不可见
    r = await client.delete(f"/api/v1/courses/{course['id']}")
    assert r.status_code == 404
    assert await db.get(Course, uuid.UUID(course["id"])) is not None


async def test_upload_validation(client: AsyncClient, db: AsyncSession, logged_in: User) -> None:
    course = await _create_course(client)
    r = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("bad.exe", b"MZ", "application/octet-stream")},
    )
    assert r.status_code == 400
    assert "不支持的文件类型" in r.json()["detail"]

    r = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert r.status_code == 400


async def test_upload_parse_ingest_flow(
    client: AsyncClient, db: AsyncSession, logged_in: User, monkeypatch
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])

    content = "# 贝叶斯定理\n\n条件概率的基本公式。\n\n## 公式\n\n$P(A|B)=P(B|A)P(A)/P(B)$\n".encode()
    r = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("bayes.md", content, "text/markdown")},
    )
    assert r.status_code == 200, r.text
    doc_id = uuid.UUID(r.json()["id"])

    # 重复上传同内容被拒
    r = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        files={"file": ("bayes-again.md", content, "text/markdown")},
    )
    assert r.status_code == 400

    # parse job 已入队 → 直接执行处理器
    parse_job = (
        await db.execute(select(Job).where(Job.kind == "parse_document"))
    ).scalar_one()
    result = await ingest_svc.handle_parse_document(db, parse_job)
    assert result["chars"] > 0
    doc = await db.get(Document, doc_id)
    await db.refresh(doc)
    assert doc.status == "parsed" and doc.parsed_md_path

    # 用假 LLM 跑 ingest
    async def fake_run_structured(self, *, response_model, **kw):
        if response_model is IngestChunkOut:
            return IngestChunkOut(
                page_updates=[
                    PageUpdate(
                        slug="bayes-theorem", title="贝叶斯定理", kind="concept",
                        content_md="贝叶斯定理是条件概率工具。参见[条件概率](wiki:conditional-probability)。",
                        change_summary="新建",
                    )
                ],
                log_entry="整合贝叶斯片段",
            )
        return IngestFinalOut(
            chapter_summary=PageUpdate(
                slug="doc-bayes-md", title="《bayes.md》总结", kind="chapter_summary",
                content_md="本文档介绍[贝叶斯定理](wiki:bayes-theorem)。",
            ),
            index_md="# 课程知识索引\n\n## 概念页\n- [贝叶斯定理](wiki:bayes-theorem) — 条件概率工具\n",
            log_entry="入库完成",
        )

    monkeypatch.setattr(runtime_module.AgentRuntime, "run_structured", fake_run_structured)

    ingest_job = (
        await db.execute(select(Job).where(Job.kind == "ingest_document"))
    ).scalar_one()
    result = await ingest_svc.handle_ingest_document(db, ingest_job)
    assert result["ingested"] == ["bayes.md"]

    await db.refresh(doc)
    assert doc.status == "ingested"

    pages = {
        p.slug: p
        for p in (
            await db.execute(select(WikiPage).where(WikiPage.course_id == course_id))
        ).scalars()
    }
    assert "bayes-theorem" in pages and "doc-bayes-md" in pages
    assert pages["bayes-theorem"].out_links == ["conditional-probability"]
    assert "贝叶斯定理" in pages["wiki-index"].content_md  # 索引已重写
    assert pages["wiki-index"].version == 2

    logs = (
        await db.execute(select(WikiLogEntry).where(WikiLogEntry.course_id == course_id))
    ).scalars().all()
    assert len(logs) == 1

    # API 侧可读
    r = await client.get(f"/api/v1/courses/{course['id']}/wiki/pages/bayes-theorem")
    assert r.status_code == 200 and r.json()["title"] == "贝叶斯定理"
    r = await client.get(f"/api/v1/courses/{course['id']}/wiki/pages/bayes-theorem/revisions")
    assert len(r.json()) == 1
