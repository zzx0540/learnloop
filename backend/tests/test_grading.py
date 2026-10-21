import uuid
from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttemptAnswer,
    Job,
    KnowledgePoint,
    Plan,
    PlanItem,
    PointsLedger,
    Question,
    Quiz,
    QuizAttempt,
    User,
    WeaknessRecord,
    WikiPage,
    utcnow,
)
from app.services import grading as grading_svc
from app.services.grading import GradeAttemptOut, GradeItemOut, WeaknessItemOut
from app.services.weakness import (
    AnswerEvidence,
    WeaknessEvidence,
    apply_answer_evidence,
    record_weaknesses,
)


async def _course_with_kps(
    client: AsyncClient, db: AsyncSession, count: int = 9
) -> tuple[dict, list[KnowledgePoint]]:
    response = await client.post(
        "/api/v1/courses",
        json={"name": "判分测试课", "goal_text": "验证判分", "daily_minutes": 30},
    )
    assert response.status_code == 200, response.text
    course = response.json()
    course_id = uuid.UUID(course["id"])
    page = WikiPage(
        course_id=course_id,
        slug="grading-source",
        title="判分材料",
        kind="concept",
        content_md="前缀缓存、判断与选择题材料。",
    )
    db.add(page)
    await db.flush()
    kps = [
        KnowledgePoint(
            course_id=course_id,
            slug=f"grade-kp-{index}",
            title=f"判分知识点 {index}",
            status="learning",
            primary_wiki_page_id=page.id,
        )
        for index in range(count)
    ]
    db.add_all(kps)
    await db.commit()
    return course, kps


def _question(
    quiz: Quiz,
    kp: KnowledgePoint,
    seq: int,
    qtype: str,
    answer: dict,
    options: list | None = None,
) -> Question:
    return Question(
        quiz_id=quiz.id,
        course_id=quiz.course_id,
        kp_id=kp.id,
        seq=seq,
        qtype=qtype,
        stem_md=f"第 {seq} 题用于验证 {qtype} 的确定性判分。",
        options=options or [],
        answer=answer,
        explanation_md="这是提交后才允许显示的解析。",
        source_refs=[{"wiki_slug": "grading-source"}],
    )


async def test_objective_grading_unanswered_and_serialization_safety(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course, kps = await _course_with_kps(client, db)
    course_id = uuid.UUID(course["id"])
    quiz = Quiz(
        course_id=course_id,
        kind="daily",
        title="客观判分卷",
        scheduled_date=date.today(),
        status="ready",
    )
    db.add(quiz)
    await db.flush()
    choice_options = [
        {"key": "A", "text": "选项 A"},
        {"key": "B", "text": "选项 B"},
        {"key": "C", "text": "选项 C"},
    ]
    questions = [
        _question(quiz, kps[0], 1, "single_choice", {"correct_options": ["A"]}, choice_options),
        _question(quiz, kps[1], 2, "single_choice", {"correct_options": ["A"]}, choice_options),
        _question(
            quiz, kps[2], 3, "multi_choice", {"correct_options": ["A", "B"]}, choice_options
        ),
        _question(
            quiz, kps[3], 4, "multi_choice", {"correct_options": ["A", "B"]}, choice_options
        ),
        _question(quiz, kps[4], 5, "true_false", {"correct_bool": True}),
        _question(quiz, kps[5], 6, "true_false", {"correct_bool": True}),
        _question(quiz, kps[6], 7, "cloze", {"accept_texts": ["Prefix Cache", "前缀缓存"]}),
        _question(quiz, kps[7], 8, "cloze", {"accept_texts": ["Prefix Cache", "前缀缓存"]}),
        _question(quiz, kps[8], 9, "single_choice", {"correct_options": ["A"]}, choice_options),
    ]
    db.add_all(questions)
    await db.commit()

    detail = await client.get(f"/api/v1/quizzes/{quiz.id}")
    assert detail.status_code == 200, detail.text
    for forbidden in (
        "correct_options",
        "rubric",
        "accept_texts",
        "explanation_md",
        "reference_md",
    ):
        assert forbidden not in detail.text

    created = await client.post(f"/api/v1/quizzes/{quiz.id}/attempts")
    assert created.status_code == 200, created.text
    attempt_id = created.json()["id"]
    submitted_answers = [
        {"selected": ["A"]},
        {"selected": ["B"]},
        {"selected": ["B", "A"]},
        {"selected": ["A", "C"]},
        {"selected_bool": True},
        {"selected_bool": False},
        {"text": "  PREFIX    cache  "},
        {"text": "其他答案"},
    ]
    for question, answer in zip(questions[:8], submitted_answers, strict=True):
        saved = await client.put(
            f"/api/v1/attempts/{attempt_id}/answers/{question.id}", json=answer
        )
        assert saved.status_code == 200, saved.text

    async def forbidden_llm(**kwargs):
        raise AssertionError("纯客观卷不得调用 LLM")

    monkeypatch.setattr(grading_svc.runtime, "run_structured", forbidden_llm)
    submitted = await client.post(f"/api/v1/attempts/{attempt_id}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "graded"
    assert submitted.json()["grading_job_id"] is None
    assert submitted.json()["total_score"] == 4
    assert submitted.json()["max_score"] == 9

    answers = list(
        (
            await db.execute(
                select(AttemptAnswer)
                .where(AttemptAnswer.attempt_id == uuid.UUID(attempt_id))
                .order_by(AttemptAnswer.question_id)
            )
        ).scalars()
    )
    by_question = {answer.question_id: answer for answer in answers}
    assert [by_question[question.id].score for question in questions] == [
        1,
        0,
        1,
        0,
        1,
        0,
        1,
        0,
        0,
    ]
    assert by_question[questions[-1].id].answer == {"selected": []}
    assert all(answer.graded_by == "auto" for answer in answers)
    objective_points = (
        await db.execute(
            select(PointsLedger).where(PointsLedger.ref_id == uuid.UUID(attempt_id))
        )
    ).scalar_one()
    assert objective_points.kind == "quiz_score" and objective_points.amount == 9

    revealed = await client.get(f"/api/v1/quizzes/{quiz.id}")
    assert revealed.status_code == 200
    assert "correct_options" in revealed.text
    assert "explanation_md" in revealed.text


async def test_subjective_job_scores_missing_items_and_filters_weaknesses(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course, kps = await _course_with_kps(client, db, 3)
    course_id = uuid.UUID(course["id"])
    quiz = Quiz(
        course_id=course_id,
        kind="daily",
        title="主观批改卷",
        scheduled_date=date.today(),
        status="ready",
    )
    db.add(quiz)
    await db.flush()
    questions = [
        _question(
            quiz,
            kps[0],
            1,
            "single_choice",
            {"correct_options": ["A"]},
            [
                {"key": "A", "text": "正确"},
                {"key": "B", "text": "错误"},
                {"key": "C", "text": "干扰"},
            ],
        ),
        _question(
            quiz,
            kps[1],
            2,
            "short_answer",
            {"rubric": "解释机制", "reference_md": "参考机制"},
        ),
        _question(
            quiz,
            kps[2],
            3,
            "short_answer",
            {"rubric": "说明应用", "reference_md": "参考应用"},
        ),
    ]
    db.add_all(questions)
    await db.commit()
    attempt = (await client.post(f"/api/v1/quizzes/{quiz.id}/attempts")).json()
    for question, answer in zip(
        questions,
        [{"selected": ["B"]}, {"text": "只答了一部分"}, {"text": "遗漏"}],
        strict=True,
    ):
        response = await client.put(
            f"/api/v1/attempts/{attempt['id']}/answers/{question.id}", json=answer
        )
        assert response.status_code == 200, response.text
    submitted = await client.post(f"/api/v1/attempts/{attempt['id']}/submit")
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["status"] == "submitted"
    job = await db.get(Job, uuid.UUID(submitted.json()["grading_job_id"]))

    async def fake_grade(**kwargs):
        assert kwargs["tier"].value == "medium"
        assert kwargs["thinking"] is False
        return GradeAttemptOut(
            items=[
                GradeItemOut(
                    question_id=questions[1].id,
                    score=0.5,
                    feedback_md="机制说明不完整。",
                )
            ],
            weaknesses=[
                WeaknessItemOut(
                    kp_slug=kps[0].slug,
                    kind="concept_confusion",
                    severity=2,
                    description="混淆了选项含义。",
                ),
                WeaknessItemOut(
                    kp_slug=kps[1].slug,
                    kind="expression_vague",
                    severity=1,
                    description="表达缺少关键机制。",
                ),
                WeaknessItemOut(
                    kp_slug="outside-pool",
                    kind="memory_lapse",
                    severity=3,
                    description="不应落库。",
                ),
            ],
        )

    monkeypatch.setattr(grading_svc.runtime, "run_structured", fake_grade)
    result = await grading_svc.handle_grade_attempt(db, job)
    assert result["score"] == 0.5
    graded = await db.get(QuizAttempt, uuid.UUID(attempt["id"]))
    assert graded.status == "graded"
    assert graded.total_score == 0.5
    assert graded.max_score == 3
    answer_rows = {
        answer.question_id: answer
        for answer in (
            await db.execute(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == graded.id)
            )
        ).scalars()
    }
    assert answer_rows[questions[1].id].feedback_md == "机制说明不完整。"
    assert answer_rows[questions[2].id].score == 0
    assert "按 0 分" in answer_rows[questions[2].id].feedback_md
    weaknesses = list(
        (
            await db.execute(
                select(WeaknessRecord).where(WeaknessRecord.course_id == course_id)
            )
        ).scalars()
    )
    assert {(record.kp_id, record.kind) for record in weaknesses} == {
        (kps[0].id, "concept_confusion"),
        (kps[1].id, "expression_vague"),
    }
    assert all(record.status == "open" for record in weaknesses)
    subjective_points = (
        await db.execute(
            select(PointsLedger).where(PointsLedger.ref_id == graded.id)
        )
    ).scalar_one()
    assert subjective_points.kind == "quiz_score" and subjective_points.amount == 3


async def test_weakness_lifecycle_merge_streak_reset_and_resolved_history(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course, kps = await _course_with_kps(client, db, 1)
    course_id = uuid.UUID(course["id"])
    record = WeaknessRecord(
        course_id=course_id,
        kp_id=kps[0].id,
        kind="memory_lapse",
        severity=1,
        seen_count=2,
        last_seen_at=utcnow() - timedelta(days=5),
    )
    db.add(record)
    await db.commit()
    await record_weaknesses(
        db,
        course_id=course_id,
        items=[
            WeaknessEvidence(
                kp_id=kps[0].id,
                kind="memory_lapse",
                severity=3,
                description="再次遗忘。",
            )
        ],
    )
    await db.flush()
    assert record.seen_count == 3
    assert record.severity == 3
    for expected_status in ("improving", "resolved"):
        await apply_answer_evidence(
            db,
            course_id=course_id,
            items=[AnswerEvidence(kp_id=kps[0].id, correct=True)],
        )
        await db.flush()
        assert record.status == expected_status
    await apply_answer_evidence(
        db,
        course_id=course_id,
        items=[AnswerEvidence(kp_id=kps[0].id, correct=False)],
    )
    assert record.status == "resolved"
    assert record.correct_streak == 2

    await record_weaknesses(
        db,
        course_id=course_id,
        items=[
            WeaknessEvidence(
                kp_id=kps[0].id,
                kind="memory_lapse",
                severity=2,
                description="新证据形成新记录。",
            )
        ],
    )
    await db.flush()
    records = list(
        (
            await db.execute(
                select(WeaknessRecord)
                .where(
                    WeaknessRecord.course_id == course_id,
                    WeaknessRecord.kind == "memory_lapse",
                )
                .order_by(WeaknessRecord.created_at)
            )
        ).scalars()
    )
    assert len(records) == 2
    fresh = next(item for item in records if item.status == "open")
    await apply_answer_evidence(
        db,
        course_id=course_id,
        items=[AnswerEvidence(kp_id=kps[0].id, correct=True)],
    )
    assert fresh.status == "improving" and fresh.correct_streak == 1
    await apply_answer_evidence(
        db,
        course_id=course_id,
        items=[AnswerEvidence(kp_id=kps[0].id, correct=False)],
    )
    assert fresh.status == "open" and fresh.correct_streak == 0


async def test_stage_submit_completes_plan_item_and_other_user_gets_404(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course, kps = await _course_with_kps(client, db, 1)
    course_id = uuid.UUID(course["id"])
    plan = Plan(
        course_id=course_id,
        version=1,
        status="active",
        goal_summary="阶段测试",
        daily_minutes=30,
        start_date=date.today(),
        rationale_md="测试",
    )
    db.add(plan)
    await db.flush()
    item = PlanItem(
        plan_id=plan.id,
        course_id=course_id,
        seq=1,
        scheduled_date=date.today(),
        type="stage_exam",
        title="阶段考",
        est_minutes=30,
    )
    db.add(item)
    await db.flush()
    quiz = Quiz(
        course_id=course_id,
        kind="stage",
        title="阶段卷",
        scheduled_date=date.today(),
        plan_item_id=item.id,
        status="ready",
    )
    db.add(quiz)
    await db.flush()
    question = _question(quiz, kps[0], 1, "true_false", {"correct_bool": True})
    db.add(question)
    await db.commit()
    attempt = (await client.post(f"/api/v1/quizzes/{quiz.id}/attempts")).json()
    saved = await client.put(
        f"/api/v1/attempts/{attempt['id']}/answers/{question.id}",
        json={"selected_bool": True},
    )
    assert saved.status_code == 200
    submitted = await client.post(f"/api/v1/attempts/{attempt['id']}/submit")
    assert submitted.status_code == 200
    await db.refresh(item)
    assert item.status == "done"
    assert item.actual_minutes is not None and item.actual_minutes <= 240

    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "other_student",
            "password": "password123",
            "nickname": "他人",
            "invite_code": "TEST",
        },
    )
    assert registered.status_code == 200, registered.text
    assert (await client.get(f"/api/v1/quizzes/{quiz.id}")).status_code == 404
    assert (await client.get(f"/api/v1/attempts/{attempt['id']}")).status_code == 404
    assert (
        await client.get(f"/api/v1/courses/{course_id}/weaknesses")
    ).status_code == 404
    assert (
        await db.execute(
            select(func.count()).select_from(QuizAttempt).where(QuizAttempt.quiz_id == quiz.id)
        )
    ).scalar_one() == 1
