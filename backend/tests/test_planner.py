import uuid
from collections import defaultdict

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Chat,
    ChatMessage,
    Job,
    KnowledgePoint,
    KpEdge,
    KpWikiLink,
    Plan,
    PlanItem,
    PlanItemKp,
    User,
    WikiPage,
)
from app.services import planner as planner_svc
from app.services.planner import (
    ExtractKpsOut,
    GeneratePlanOut,
    KpEdgeItem,
    KpItem,
    OnboardingSummary,
    PlanItemOut,
)


async def _create_course(client: AsyncClient) -> dict:
    response = await client.post(
        "/api/v1/courses",
        json={"name": "学习科学入门", "goal_text": "掌握核心学习方法", "daily_minutes": 45},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_extract_kps_upserts_links_and_edges(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    page_a = WikiPage(
        course_id=course_id,
        slug="spacing-effect",
        title="间隔效应",
        kind="concept",
        content_md="间隔学习能提升长期保持。",
    )
    page_b = WikiPage(
        course_id=course_id,
        slug="retrieval-practice",
        title="提取练习",
        kind="concept",
        content_md="主动提取比重复阅读更有效。",
    )
    db.add_all([page_a, page_b])
    await db.commit()

    outputs = [
        ExtractKpsOut(
            knowledge_points=[
                KpItem(
                    slug="spacing-effect",
                    title="间隔效应",
                    summary="分散安排学习。",
                    importance=5,
                    difficulty=3,
                    primary_wiki_slug=None,
                ),
                KpItem(
                    slug="retrieval-practice",
                    title="提取练习",
                    summary="通过回忆强化学习。",
                    importance=4,
                    difficulty=4,
                    primary_wiki_slug="retrieval-practice",
                ),
            ],
            edges=[
                KpEdgeItem(
                    src_slug="spacing-effect",
                    dst_slug="retrieval-practice",
                    relation="related",
                )
            ],
        ),
        ExtractKpsOut(
            knowledge_points=[
                KpItem(
                    slug="spacing-effect",
                    title="间隔效应重复名",
                    summary="更新后的摘要。",
                    importance=2,
                    difficulty=2,
                    primary_wiki_slug="spacing-effect",
                ),
                KpItem(
                    slug="retrieval-practice",
                    title="提取练习",
                    summary="更新提取摘要。",
                    importance=5,
                    difficulty=5,
                    primary_wiki_slug="retrieval-practice",
                ),
            ],
            edges=[
                KpEdgeItem(
                    src_slug="spacing-effect",
                    dst_slug="retrieval-practice",
                    relation="related",
                )
            ],
        ),
    ]

    async def fake_run_structured(**kwargs):
        return outputs.pop(0)

    monkeypatch.setattr(planner_svc.runtime, "run_structured", fake_run_structured)
    for _ in range(2):
        job = Job(kind="extract_kps", payload={"course_id": str(course_id)}, course_id=course_id)
        db.add(job)
        await db.flush()
        await planner_svc.handle_extract_kps(db, job)

    kps = list(
        (
            await db.execute(
                select(KnowledgePoint)
                .where(KnowledgePoint.course_id == course_id)
                .order_by(KnowledgePoint.slug)
            )
        ).scalars()
    )
    assert len(kps) == 2
    spacing = next(kp for kp in kps if kp.slug == "spacing-effect")
    retrieval = next(kp for kp in kps if kp.slug == "retrieval-practice")
    assert spacing.title == "间隔效应"
    assert spacing.summary == "更新后的摘要。"
    assert spacing.importance == 5 and spacing.difficulty == 3
    assert spacing.primary_wiki_page_id == page_a.id
    assert retrieval.importance == 5 and retrieval.difficulty == 5
    assert (
        await db.execute(select(func.count()).select_from(KpEdge).where(KpEdge.course_id == course_id))
    ).scalar_one() == 1
    assert (
        await db.execute(select(func.count()).select_from(KpWikiLink))
    ).scalar_one() == 2


async def test_generate_plan_repairs_order_budget_and_versions(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course_data = await _create_course(client)
    course_id = uuid.UUID(course_data["id"])
    page_a = WikiPage(
        course_id=course_id,
        slug="foundation",
        title="基础概念正文",
        kind="concept",
        content_md="# 基础概念正文\n\n这是基础概念的关键材料，包含定义、边界和一个应用例子。",
    )
    page_b = WikiPage(
        course_id=course_id,
        slug="application",
        title="综合应用正文",
        kind="concept",
        content_md="# 综合应用正文\n\n这是综合应用的关键材料，包含步骤、约束和一个练习场景。",
    )
    db.add_all([page_a, page_b])
    await db.flush()
    kp_a = KnowledgePoint(
        course_id=course_id,
        slug="foundation",
        title="基础概念",
        importance=5,
        difficulty=2,
        primary_wiki_page_id=page_a.id,
    )
    kp_b = KnowledgePoint(
        course_id=course_id,
        slug="application",
        title="综合应用",
        importance=5,
        difficulty=4,
        primary_wiki_page_id=page_b.id,
    )
    db.add_all([kp_a, kp_b])
    await db.flush()
    db.add(
        KpEdge(
            course_id=course_id,
            src_kp_id=kp_a.id,
            dst_kp_id=kp_b.id,
            relation="prerequisite",
        )
    )
    chat = Chat(course_id=course_id, user_id=logged_in.id, agent="planner", title="规划")
    db.add(chat)
    await db.flush()
    db.add(
        ChatMessage(
            chat_id=chat.id,
            role="user",
            content_md="我每天学习45分钟，希望循序渐进掌握并能实际应用。",
        )
    )
    await db.commit()

    async def fake_run_structured(*, response_model, **kwargs):
        if response_model is OnboardingSummary:
            stable = kwargs["stable_blocks"][0]
            assert "当前每日可用：45 分钟" in stable
            assert "当前目标日期：未设置" in stable
            return OnboardingSummary(
                goals="掌握学习科学并能应用",
                daily_minutes=45,
                target_date=None,
                prior_knowledge="了解基础术语",
                preferences="短时专注",
            )
        return GeneratePlanOut(
            rationale_md="先基础后应用，并穿插测验。",
            items=[
                PlanItemOut(
                    date_offset=0,
                    type="learn",
                    title="应用组",
                    kp_slugs=["application"],
                    est_minutes=40,
                ),
                PlanItemOut(
                    date_offset=0,
                    type="learn",
                    title="基础组",
                    kp_slugs=["foundation"],
                    est_minutes=40,
                ),
            ],
        )

    monkeypatch.setattr(planner_svc.runtime, "run_structured", fake_run_structured)
    for version in (1, 2):
        job = Job(kind="generate_plan", payload={"course_id": str(course_id)}, course_id=course_id)
        db.add(job)
        await db.flush()
        result = await planner_svc.handle_generate_plan(db, job)
        assert result["version"] == version

    plans = list(
        (
            await db.execute(select(Plan).where(Plan.course_id == course_id).order_by(Plan.version))
        ).scalars()
    )
    assert [plan.status for plan in plans] == ["superseded", "active"]
    active = plans[-1]
    items = list(
        (
            await db.execute(
                select(PlanItem).where(PlanItem.plan_id == active.id).order_by(PlanItem.seq)
            )
        ).scalars()
    )
    loads: dict = defaultdict(int)
    learn_loads: dict = defaultdict(int)
    for item in items:
        loads[item.scheduled_date] += item.est_minutes
        if item.type == "learn":
            learn_loads[item.scheduled_date] += item.est_minutes
    assert max(loads.values()) <= 45
    assert max(learn_loads.values()) <= int(45 * 0.7)
    assert any(item.type == "quiz" for item in items)
    assert all(item.description_md for item in items)
    assert any("学习目标" in item.description_md for item in items if item.type == "learn")
    assert any("基础概念的关键材料" in item.description_md for item in items)
    assert any("综合应用的关键材料" in item.description_md for item in items)
    kp_sequence = {
        kp_id: seq
        for kp_id, seq in (
            await db.execute(
                select(PlanItemKp.kp_id, PlanItem.seq)
                .join(PlanItem, PlanItem.id == PlanItemKp.plan_item_id)
                .where(PlanItem.plan_id == active.id)
            )
        ).all()
    }
    assert kp_sequence[kp_a.id] < kp_sequence[kp_b.id]
    dates = sorted(loads)
    assert all((right - left).days == 1 for left, right in zip(dates, dates[1:], strict=False))

    projection_job = Job(
        kind="project_mastery",
        payload={"course_id": str(course_id)},
        course_id=course_id,
    )
    db.add(projection_job)
    await db.flush()
    await planner_svc.handle_project_mastery(db, projection_job)
    await db.refresh(active)
    assert active.projected_basic_date is not None
    assert active.projected_full_date is not None
    assert active.projected_basic_date < active.projected_full_date

    response = await client.get(f"/api/v1/courses/{course_id}/overview")
    assert response.status_code == 200
    assert response.json()["projected_basic_date"] is not None
    plan_response = await client.get(f"/api/v1/courses/{course_id}/plan")
    assert plan_response.status_code == 200
    assert plan_response.json()["version"] == 2
    revise_response = await client.post(f"/api/v1/courses/{course_id}/plan/generate")
    assert revise_response.status_code == 200
    assert revise_response.json()["kind"] == "revise_plan"


def test_learning_index_keeps_secondary_pages_out_of_core_path() -> None:
    course_id = uuid.uuid4()
    core_page = WikiPage(
        id=uuid.uuid4(),
        course_id=course_id,
        slug="core-page",
        title="核心页面",
        kind="concept",
        content_md="核心页面正文。",
    )
    secondary_page = WikiPage(
        id=uuid.uuid4(),
        course_id=course_id,
        slug="secondary-page",
        title="次要页面",
        kind="concept",
        content_md="次要页面正文。",
    )
    chapter = WikiPage(
        id=uuid.uuid4(),
        course_id=course_id,
        slug="chapter-summary",
        title="章节总结",
        kind="chapter_summary",
        content_md="章节总结正文。",
    )
    kp = KnowledgePoint(
        id=uuid.uuid4(),
        course_id=course_id,
        slug="core-kp",
        title="核心知识点",
        summary="核心摘要",
        primary_wiki_page_id=core_page.id,
    )
    index = planner_svc.build_learning_index(
        [core_page, secondary_page, chapter], [kp], {"core-kp"}
    )
    assert "wiki:core-page" in index
    assert "wiki:chapter-summary" in index
    assert "wiki:secondary-page" not in index


async def test_planner_chat_sse_and_history(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    course_id = course["id"]
    response = await client.post(f"/api/v1/courses/{course_id}/chats", json={"agent": "planner"})
    assert response.status_code == 200, response.text
    chat_id = response.json()["id"]

    async def fake_run_stream(**kwargs):
        yield "请告诉我你的学习目标。"
        yield "每天可以学习多久？"

    monkeypatch.setattr(planner_svc.runtime, "run_stream", fake_run_stream)
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "我想系统学习这门课。"},
    )
    assert response.status_code == 200
    assert "event: delta" in response.text
    assert "event: done" in response.text

    response = await client.get(f"/api/v1/chats/{chat_id}/messages")
    assert response.status_code == 200
    messages = response.json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[-1]["content_md"] == "请告诉我你的学习目标。每天可以学习多久？"
    response = await client.get(f"/api/v1/courses/{course_id}/chats?agent=planner")
    assert len(response.json()) == 1

    async def broken_run_stream(**kwargs):
        yield "部分回复"
        raise RuntimeError("stream interrupted")

    monkeypatch.setattr(planner_svc.runtime, "run_stream", broken_run_stream)
    response = await client.post(
        f"/api/v1/chats/{chat_id}/messages",
        json={"content": "请继续。"},
    )
    assert response.status_code == 200
    assert "event: error" in response.text
    assert "AI 回复中断，请重试" in response.text
    assert "event: done" not in response.text
