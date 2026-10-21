import uuid
from datetime import date, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CourseBrief,
    Job,
    KnowledgePoint,
    Plan,
    PlanItem,
    PlanItemKp,
    Question,
    Quiz,
    QuizAttempt,
    ReviewCard,
    User,
    WeaknessRecord,
    WikiPage,
    utcnow,
)
from app.services import examiner as examiner_svc
from app.services import planner as planner_svc
from app.services.course_kind import KIND_HINT_V1
from app.services.examiner import (
    GenerateQuizOut,
    OptionOut,
    PoolEntry,
    QcItemOut,
    QuestionGenOut,
    QuestionQcOut,
    RecallPromptItemOut,
    RecallPromptsOut,
)


async def _create_course(
    client: AsyncClient, name: str = "考核测试课", *, kind: str = "knowledge"
) -> dict:
    response = await client.post(
        "/api/v1/courses",
        json={
            "name": name,
            "goal_text": "掌握课程核心内容",
            "daily_minutes": 45,
            "kind": kind,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _add_kps(
    db: AsyncSession, course_id: uuid.UUID, count: int
) -> list[KnowledgePoint]:
    pages = [
        WikiPage(
            course_id=course_id,
            slug=f"wiki-{index}",
            title=f"页面 {index}",
            kind="concept",
            content_md=f"知识点 {index} 的课程材料，包含定义、辨析和应用。",
        )
        for index in range(count)
    ]
    db.add_all(pages)
    await db.flush()
    kps = [
        KnowledgePoint(
            course_id=course_id,
            slug=f"kp-{index}",
            title=f"知识点 {index}",
            summary=f"摘要 {index}",
            importance=5 - index % 3,
            status="learning",
            primary_wiki_page_id=pages[index].id,
        )
        for index in range(count)
    ]
    db.add_all(kps)
    await db.commit()
    return kps


async def test_daily_pool_enforces_ratio_types_dedupe_and_minimum(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kps = await _add_kps(db, course_id, 8)
    plan = Plan(
        course_id=course_id,
        version=1,
        status="active",
        goal_summary="测试",
        daily_minutes=45,
        start_date=date.today() - timedelta(days=2),
        rationale_md="测试",
    )
    db.add(plan)
    await db.flush()
    for index, kp in enumerate(kps[5:], start=1):
        item = PlanItem(
            plan_id=plan.id,
            course_id=course_id,
            seq=index,
            scheduled_date=date.today() - timedelta(days=1),
            type="learn",
            title=kp.title,
            est_minutes=10,
            status="done",
        )
        db.add(item)
        await db.flush()
        db.add(PlanItemKp(plan_item_id=item.id, kp_id=kp.id))
    for index, kp in enumerate(kps[:5]):
        db.add(
            WeaknessRecord(
                course_id=course_id,
                kp_id=kp.id,
                kind="concept_confusion",
                severity=3 if index < 3 else 1,
                last_seen_at=utcnow() - timedelta(minutes=index),
            )
        )
    for index, kp in enumerate(kps):
        db.add(
            ReviewCard(
                course_id=course_id,
                kp_id=kp.id,
                stability=1 + index / 10,
                due=utcnow(),
                state=2,
                fsrs_card={},
            )
        )
    await db.commit()

    pool = await examiner_svc.select_daily_pool(db, course_id, date.today())

    assert pool is not None
    assert 5 <= len(pool) <= 8
    assert len({entry.kp_slug for entry in pool}) == len(pool)
    weakness_count = sum(entry.source == "weakness" for entry in pool)
    assert 0.3 <= weakness_count / len(pool) <= 0.4
    assert all(
        entry.qtype == "short_answer"
        for entry in pool
        if entry.source == "weakness"
    )
    assert 1 <= sum(entry.qtype == "short_answer" for entry in pool) <= 2

    small_course = await _create_course(client, "候选不足课")
    small_id = uuid.UUID(small_course["id"])
    small_kps = await _add_kps(db, small_id, 2)
    for kp in small_kps:
        db.add(
            ReviewCard(
                course_id=small_id,
                kp_id=kp.id,
                stability=1,
                due=utcnow(),
                state=1,
                fsrs_card={},
            )
        )
    await db.commit()
    assert await examiner_svc.select_daily_pool(db, small_id, date.today()) is None


def _valid_question(entry: PoolEntry, wiki_slug: str) -> QuestionGenOut:
    common = {
        "kp_slug": entry.kp_slug,
        "qtype": entry.qtype,
        "stem_md": f"请根据课程材料回答关于 {entry.kp_slug} 的这个问题。",
        "explanation_md": "答案可由课程材料直接核验。",
        "source_wiki_slugs": [wiki_slug],
    }
    if entry.qtype == "single_choice":
        return QuestionGenOut(
            **common,
            options=[
                OptionOut(key="A", text="正确描述"),
                OptionOut(key="B", text="错误描述一"),
                OptionOut(key="C", text="错误描述二"),
            ],
            correct_options=["A"],
        )
    if entry.qtype == "multi_choice":
        return QuestionGenOut(
            **common,
            options=[
                OptionOut(key="A", text="正确描述一"),
                OptionOut(key="B", text="正确描述二"),
                OptionOut(key="C", text="错误描述"),
            ],
            correct_options=["A", "B"],
        )
    if entry.qtype == "true_false":
        return QuestionGenOut(**common, correct_bool=True)
    if entry.qtype == "cloze":
        return QuestionGenOut(
            **{**common, "stem_md": f"{entry.kp_slug} 的核心机制是 ____。"},
            accept_texts=["前缀缓存"],
        )
    return QuestionGenOut(
        **common,
        rubric="说明核心概念与作用机制。",
        reference_md="核心概念会改善长期保持。",
    )


def test_cloze_validation_converts_anki_syntax_and_requires_blank() -> None:
    entry = PoolEntry(kp_slug="kp-0", source="weakest", qtype="cloze")
    common = {
        "kp_slug": "kp-0",
        "qtype": "cloze",
        "explanation_md": "答案可由课程材料直接核验。",
        "source_wiki_slugs": ["wiki-0"],
    }

    single = QuestionGenOut(
        **common,
        stem_md="间隔学习的核心方法是 {{c1::分散学习}}。",
        accept_texts=["间隔重复"],
    )
    checked, reason = examiner_svc._validate_generated(single, entry, {"wiki-0"})
    assert reason is None
    assert checked is not None
    assert checked["item"].stem_md == "间隔学习的核心方法是 ____。"
    assert checked["answer"]["accept_texts"] == ["分散学习", "间隔重复"]

    multiple = QuestionGenOut(
        **common,
        stem_md="{{c1::分散学习}}需要结合{{c2::主动回忆}}。",
        accept_texts=["间隔练习"],
    )
    checked, reason = examiner_svc._validate_generated(multiple, entry, {"wiki-0"})
    assert reason is None
    assert checked is not None
    assert checked["item"].stem_md == "____需要结合主动回忆。"
    assert checked["answer"]["accept_texts"] == ["分散学习", "间隔练习"]

    clean = QuestionGenOut(
        **common,
        stem_md="间隔学习的核心方法是 ____。",
        accept_texts=["分散学习"],
    )
    checked, reason = examiner_svc._validate_generated(clean, entry, {"wiki-0"})
    assert reason is None
    assert checked is not None
    assert checked["item"].stem_md == "间隔学习的核心方法是 ____。"

    missing_blank = QuestionGenOut(
        **common,
        stem_md="间隔学习的核心方法是分散学习。",
        accept_texts=["分散学习"],
    )
    checked, reason = examiner_svc._validate_generated(missing_blank, entry, {"wiki-0"})
    assert checked is None
    assert reason == "填空题缺少空缺标记"


async def test_generation_drops_untrusted_items_and_retries_below_sixty_percent(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kps = await _add_kps(db, course_id, 6)
    pool = [
        PoolEntry(kp_slug="kp-0", source="weakest", qtype="single_choice"),
        PoolEntry(kp_slug="kp-1", source="weakness", qtype="short_answer"),
        PoolEntry(kp_slug="kp-2", source="weakest", qtype="true_false"),
        PoolEntry(kp_slug="kp-3", source="weakest", qtype="cloze"),
        PoolEntry(kp_slug="kp-4", source="weakest", qtype="multi_choice"),
        PoolEntry(kp_slug="kp-5", source="weakest", qtype="single_choice"),
    ]

    async def fake_pool(*args, **kwargs):
        return pool

    valid = [_valid_question(entry, f"wiki-{index}") for index, entry in enumerate(pool)]
    valid[0].correct_options = ["A", "B"]
    valid[1].rubric = ""
    valid[2].source_wiki_slugs = ["not-in-course"]
    outside = _valid_question(
        PoolEntry(kp_slug="outside", source="weakest", qtype="true_false"),
        "wiki-0",
    )

    async def fake_structured(**kwargs):
        if kwargs["response_model"] is GenerateQuizOut:
            return GenerateQuizOut(questions=[outside, *valid])
        return QuestionQcOut(
            items=[QcItemOut(seq=index, ok=True) for index in range(1, 4)]
        )

    monkeypatch.setattr(examiner_svc, "select_daily_pool", fake_pool)
    monkeypatch.setattr(examiner_svc.runtime, "run_structured", fake_structured)
    job = Job(
        kind="generate_quiz",
        payload={"course_id": str(course_id), "quiz_date": date.today().isoformat()},
        course_id=course_id,
    )
    db.add(job)
    await db.flush()
    job_id = job.id

    with pytest.raises(ValueError, match="低于最低要求"):
        await examiner_svc.handle_generate_quiz(db, job)

    quiz = (
        await db.execute(select(Quiz).where(Quiz.created_by_job_id == job_id))
    ).scalar_one()
    assert quiz.status == "failed"
    reasons = {item["reason"] for item in quiz.gen_spec["dropped"]}
    assert "知识点不在代码选池" in reasons
    assert "单选题必须恰有一个正确选项" in reasons
    assert "主观题缺少评分标准或参考答案" in reasons
    assert "无课程内有效溯源" in reasons
    assert (
        await db.execute(select(func.count()).select_from(Question).where(Question.quiz_id == quiz.id))
    ).scalar_one() == 0
    assert len(kps) == 6


async def test_qc_drop_persists_ready_quiz_and_audit(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client, kind="language")
    course_id = uuid.UUID(course["id"])
    await _add_kps(db, course_id, 5)
    qtypes = ["short_answer", "single_choice", "true_false", "cloze", "multi_choice"]
    pool = [
        PoolEntry(kp_slug=f"kp-{index}", source="weakest", qtype=qtype)
        for index, qtype in enumerate(qtypes)
    ]

    async def fake_pool(*args, **kwargs):
        return pool

    generation_prompt = ""

    async def fake_structured(**kwargs):
        nonlocal generation_prompt
        if kwargs["response_model"] is GenerateQuizOut:
            generation_prompt = kwargs["system_prompt"]
            return GenerateQuizOut(
                questions=[
                    _valid_question(entry, f"wiki-{index}")
                    for index, entry in enumerate(pool)
                ]
            )
        return QuestionQcOut(
            items=[
                QcItemOut(seq=index, ok=index != 2, reason="选项有歧义" if index == 2 else "")
                for index in range(1, 6)
            ]
        )

    monkeypatch.setattr(examiner_svc, "select_daily_pool", fake_pool)
    monkeypatch.setattr(examiner_svc.runtime, "run_structured", fake_structured)
    job = Job(
        kind="generate_quiz",
        payload={"course_id": str(course_id), "quiz_date": date.today().isoformat()},
        course_id=course_id,
    )
    db.add(job)
    await db.flush()
    result = await examiner_svc.handle_generate_quiz(db, job)

    assert result["questions"] == 4
    quiz = await db.get(Quiz, uuid.UUID(result["quiz_id"]))
    assert quiz.status == "ready"
    assert quiz.gen_spec["weakness_ratio_target"] == [0.3, 0.4]
    assert quiz.gen_spec["qc_dropped"] == [
        {"kp_slug": "kp-1", "reason": "选项有歧义"}
    ]
    assert generation_prompt.endswith(KIND_HINT_V1["language"])
    questions = list(
        (
            await db.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.seq)
            )
        ).scalars()
    )
    assert [question.seq for question in questions] == [1, 2, 3, 4]
    assert all(question.source_refs[0]["wiki_slug"].startswith("wiki-") for question in questions)


async def test_recall_backfill_weekly_summary_and_overview(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kps = await _add_kps(db, course_id, 2)
    kps[1].recall_prompt = "已有问题不能覆盖"
    db.add(
        WeaknessRecord(
            course_id=course_id,
            kp_id=kps[0].id,
            kind="memory_lapse",
            severity=2,
        )
    )
    await db.commit()

    async def fake_recall(**kwargs):
        return RecallPromptsOut(
            items=[
                RecallPromptItemOut(kp_slug="kp-0", recall_prompt="你能回忆起核心机制吗？"),
                RecallPromptItemOut(kp_slug="kp-1", recall_prompt="这条不应覆盖。"),
            ]
        )

    monkeypatch.setattr(examiner_svc.runtime, "run_structured", fake_recall)
    recall_job = Job(
        kind="backfill_recall_prompts",
        payload={"course_id": str(course_id)},
        course_id=course_id,
    )
    db.add(recall_job)
    await db.flush()
    assert (await examiner_svc.handle_backfill_recall_prompts(db, recall_job))["updated"] == 1
    assert kps[0].recall_prompt == "你能回忆起核心机制吗？"
    assert kps[1].recall_prompt == "已有问题不能覆盖"

    summaries = iter(["第一周总结", "第二周总结"])

    async def fake_text(**kwargs):
        return next(summaries)

    monkeypatch.setattr(planner_svc.runtime, "run_text", fake_text)
    for _ in range(2):
        summary_job = Job(
            kind="weekly_summary",
            payload={"course_id": str(course_id)},
            course_id=course_id,
        )
        db.add(summary_job)
        await db.flush()
        await planner_svc.handle_weekly_summary(db, summary_job)
    briefs = list(
        (
            await db.execute(
                select(CourseBrief)
                .where(
                    CourseBrief.course_id == course_id,
                    CourseBrief.kind == "progress_summary",
                )
                .order_by(CourseBrief.version)
            )
        ).scalars()
    )
    assert [(brief.version, brief.content_md) for brief in briefs] == [
        (1, "第一周总结"),
        (2, "第二周总结"),
    ]

    quiz = Quiz(
        course_id=course_id,
        kind="daily",
        title="今日测验",
        scheduled_date=date.today(),
        status="ready",
    )
    db.add(quiz)
    await db.flush()
    db.add(
        QuizAttempt(
            course_id=course_id,
            user_id=logged_in.id,
            quiz_id=quiz.id,
            kind="daily",
        )
    )
    await db.commit()
    overview = await client.get(f"/api/v1/courses/{course_id}/overview")
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data["latest_progress_summary_md"] == "第二周总结"
    assert data["open_weakness_count"] == 1
    assert data["today_quiz"]["quiz_id"] == str(quiz.id)
