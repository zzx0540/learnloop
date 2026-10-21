import hashlib
import json
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.worker import scan_expired_formal_exams
from app.models import (
    AttemptAnswer,
    ExamEvent,
    Job,
    KnowledgePoint,
    Question,
    Quiz,
    QuizAttempt,
    User,
    WikiPage,
    utcnow,
)
from app.services import examiner as examiner_svc
from app.services import grading as grading_svc
from app.services.examiner import (
    GenerateQuizOut,
    OptionOut,
    PoolEntry,
    QcItemOut,
    QuestionGenOut,
    QuestionQcOut,
)
from app.services.grading import GradeAttemptOut, GradeItemOut

SHANGHAI = ZoneInfo("Asia/Shanghai")


async def _create_course(client: AsyncClient, name: str = "正式考试测试课") -> dict:
    response = await client.post(
        "/api/v1/courses",
        json={"name": name, "goal_text": "验证正式考试", "daily_minutes": 45},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _add_learned_kps(
    db: AsyncSession, course_id: uuid.UUID, count: int, *, offset: int = 0
) -> list[KnowledgePoint]:
    pages = [
        WikiPage(
            course_id=course_id,
            slug=f"exam-wiki-{index}",
            title=f"考试材料 {index}",
            kind="concept",
            content_md=f"知识点 {index} 的定义、机制与应用材料。",
        )
        for index in range(offset, offset + count)
    ]
    db.add_all(pages)
    await db.flush()
    kps = [
        KnowledgePoint(
            course_id=course_id,
            slug=f"exam-kp-{index}",
            title=f"考试知识点 {index}",
            summary=f"摘要 {index}",
            importance=5 - index % 4,
            status="learning",
            first_learned_at=utcnow() - timedelta(days=index + 1),
            primary_wiki_page_id=page.id,
        )
        for page, index in zip(pages, range(offset, offset + count), strict=True)
    ]
    db.add_all(kps)
    await db.commit()
    return kps


def _generated_question(entry: PoolEntry, wiki_slug: str) -> QuestionGenOut:
    common = {
        "kp_slug": entry.kp_slug,
        "qtype": entry.qtype,
        "stem_md": f"请依据课程材料回答关于 {entry.kp_slug} 的正式考试问题。",
        "explanation_md": "答案可以从课程材料中直接核验。",
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
            accept_texts=["核心机制"],
        )
    return QuestionGenOut(
        **common,
        rubric="准确解释核心概念与应用。",
        reference_md="课程材料中的核心概念与应用说明。",
    )


def _question(
    quiz: Quiz,
    kp: KnowledgePoint,
    seq: int,
    qtype: str = "single_choice",
) -> Question:
    if qtype == "short_answer":
        answer = {"rubric": "说明核心机制", "reference_md": "核心机制参考答案"}
        options = []
    else:
        answer = {"correct_options": ["A"]}
        options = [
            {"key": "A", "text": "正确"},
            {"key": "B", "text": "错误"},
            {"key": "C", "text": "干扰"},
        ]
    return Question(
        quiz_id=quiz.id,
        course_id=quiz.course_id,
        kp_id=kp.id,
        seq=seq,
        qtype=qtype,
        stem_md=f"第 {seq} 题用于验证正式考试。",
        options=options,
        answer=answer,
        explanation_md="提交后解析",
        source_refs=[{"wiki_slug": "exam-wiki-0"}],
    )


async def _ready_quiz(
    db: AsyncSession,
    course_id: uuid.UUID,
    kp: KnowledgePoint,
    *,
    kind: str = "formal_exam",
    time_limit_seconds: int = 1800,
    subjective: bool = False,
) -> tuple[Quiz, list[Question]]:
    quiz = Quiz(
        course_id=course_id,
        kind=kind,
        title="正式考试卷" if kind == "formal_exam" else "每日卷",
        scheduled_date=date.today(),
        status="ready",
        time_limit_seconds=time_limit_seconds if kind == "formal_exam" else None,
    )
    db.add(quiz)
    await db.flush()
    questions = [_question(quiz, kp, 1)]
    if subjective:
        questions.append(_question(quiz, kp, 2, "short_answer"))
    db.add_all(questions)
    await db.commit()
    return quiz, questions


async def test_booking_dedupe_formal_generation_and_time_limit(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kps = await _add_learned_kps(db, course_id, 10)
    exam_date = datetime.now(SHANGHAI).date().isoformat()

    first = await client.post(
        f"/api/v1/courses/{course_id}/exams/book", json={"date": exam_date}
    )
    second = await client.post(
        f"/api/v1/courses/{course_id}/exams/book", json={"date": exam_date}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    job = await db.get(Job, uuid.UUID(first.json()["id"]))
    assert job.payload["exam_kind"] == "formal_exam"

    pool = await examiner_svc.select_formal_pool(db, course_id)
    assert pool is not None
    assert len(pool) == 15
    assert sum(entry.qtype == "short_answer" for entry in pool) == 3
    assert all(entry.source == "exam" for entry in pool)
    kp_by_slug = {kp.slug: index for index, kp in enumerate(kps)}
    tiers = []
    generation_prompts = []

    async def fake_structured(**kwargs):
        tiers.append(kwargs["tier"])
        if kwargs["response_model"] is GenerateQuizOut:
            generation_prompts.append(kwargs["system_prompt"])
            return GenerateQuizOut(
                questions=[
                    _generated_question(entry, f"exam-wiki-{kp_by_slug[entry.kp_slug]}")
                    for entry in pool
                ]
            )
        return QuestionQcOut(
            items=[QcItemOut(seq=index, ok=True) for index in range(1, 16)]
        )

    monkeypatch.setattr(examiner_svc.runtime, "run_structured", fake_structured)
    result = await examiner_svc.handle_generate_quiz(db, job)
    quiz = await db.get(Quiz, uuid.UUID(result["quiz_id"]))
    assert result["questions"] == 15
    assert quiz.kind == "formal_exam"
    assert quiz.plan_item_id is None
    assert quiz.time_limit_seconds == 12 * 90 + 3 * 480 == 2520
    assert quiz.gen_spec["time_limit_seconds"] == 2520
    assert quiz.gen_spec["objective_count"] == 12
    assert quiz.gen_spec["subjective_count"] == 3
    assert tiers[0].value == "high"
    assert generation_prompts == [examiner_svc.EXAMINER_SYSTEM_V2]
    assert hashlib.sha256(generation_prompts[0].encode()).hexdigest() == (
        "a3fded87af200c81aedeced57464e2ec37c1af3c54a3b49c3012ea27676c6f10"
    )
    assert examiner_svc.calculate_time_limit(0, 0) == 1800
    assert examiner_svc.calculate_time_limit(100, 0) == 7200


async def test_formal_generation_skips_below_ten_learned_kps(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client, "知识点不足课")
    course_id = uuid.UUID(course["id"])
    await _add_learned_kps(db, course_id, 9)
    job = Job(
        kind="generate_quiz",
        payload={
            "course_id": str(course_id),
            "quiz_date": date.today().isoformat(),
            "exam_kind": "formal_exam",
        },
        course_id=course_id,
        user_id=logged_in.id,
    )
    db.add(job)
    await db.flush()

    result = await examiner_svc.handle_generate_quiz(db, job)

    assert result == {"skipped": "已学知识点不足，无法组卷"}
    assert (
        await db.execute(select(func.count()).select_from(Quiz).where(Quiz.course_id == course_id))
    ).scalar_one() == 0


async def test_skipped_formal_job_can_rebook_and_generate(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client, "跳过后重试课")
    course_id = uuid.UUID(course["id"])
    kps = await _add_learned_kps(db, course_id, 9)
    exam_date = datetime.now(SHANGHAI).date().isoformat()

    first = await client.post(
        f"/api/v1/courses/{course_id}/exams/book", json={"date": exam_date}
    )
    assert first.status_code == 200
    first_job = await db.get(Job, uuid.UUID(first.json()["id"]))
    assert await examiner_svc.handle_generate_quiz(db, first_job) == {
        "skipped": "已学知识点不足，无法组卷"
    }
    first_job.status = "succeeded"
    await db.commit()

    kps.extend(await _add_learned_kps(db, course_id, 1, offset=9))
    second = await client.post(
        f"/api/v1/courses/{course_id}/exams/book", json={"date": exam_date}
    )
    assert second.status_code == 200
    assert second.json()["id"] != first.json()["id"]
    second_job = await db.get(Job, uuid.UUID(second.json()["id"]))
    pool = await examiner_svc.select_formal_pool(db, course_id)
    assert pool is not None
    kp_by_slug = {kp.slug: index for index, kp in enumerate(kps)}

    async def fake_structured(**kwargs):
        if kwargs["response_model"] is GenerateQuizOut:
            return GenerateQuizOut(
                questions=[
                    _generated_question(entry, f"exam-wiki-{kp_by_slug[entry.kp_slug]}")
                    for entry in pool
                ]
            )
        return QuestionQcOut(
            items=[QcItemOut(seq=index, ok=True) for index in range(1, len(pool) + 1)]
        )

    monkeypatch.setattr(examiner_svc.runtime, "run_structured", fake_structured)
    generated = await examiner_svc.handle_generate_quiz(db, second_job)
    assert generated["questions"] == 15


async def test_daily_job_reuses_active_or_quiz_backed_terminal_job(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client, "每日任务复用课")
    course_id = uuid.UUID(course["id"])
    quiz_date = datetime.now(SHANGHAI).date()
    dedupe_key = f"daily_quiz:{course_id}:{quiz_date.isoformat()}"
    skipped = Job(
        kind="generate_quiz",
        status="succeeded",
        payload={"course_id": str(course_id), "quiz_date": quiz_date.isoformat()},
        course_id=course_id,
        user_id=logged_in.id,
        dedupe_key=dedupe_key,
    )
    db.add(skipped)
    await db.commit()

    created = await client.post(f"/api/v1/courses/{course_id}/quizzes/generate-daily")
    assert created.status_code == 200
    assert created.json()["id"] != str(skipped.id)
    active_job_id = uuid.UUID(created.json()["id"])

    repeated = await client.post(f"/api/v1/courses/{course_id}/quizzes/generate-daily")
    assert repeated.status_code == 200
    assert repeated.json()["id"] == str(active_job_id)

    active_job = await db.get(Job, active_job_id)
    active_job.status = "failed"
    db.add(
        Quiz(
            course_id=course_id,
            kind="daily",
            title="生成失败的每日卷",
            scheduled_date=quiz_date,
            status="failed",
            created_by_job_id=active_job.id,
        )
    )
    await db.commit()

    failed = await client.post(f"/api/v1/courses/{course_id}/quizzes/generate-daily")
    assert failed.status_code == 200
    assert failed.json()["id"] == str(active_job_id)


async def test_attempt_deadline_is_one_time_server_now_and_chat_guard(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kp = (await _add_learned_kps(db, course_id, 1))[0]
    quiz, _ = await _ready_quiz(db, course_id, kp, time_limit_seconds=1800)
    chat = await client.post(
        f"/api/v1/courses/{course_id}/chats", json={"agent": "planner"}
    )
    assert chat.status_code == 200, chat.text

    before = utcnow()
    first = await client.post(f"/api/v1/quizzes/{quiz.id}/attempts")
    assert first.status_code == 200, first.text
    payload = first.json()
    deadline = datetime.fromisoformat(payload["deadline_at"])
    started = datetime.fromisoformat(payload["started_at"])
    server_now = datetime.fromisoformat(payload["server_now"])
    assert abs((deadline - started).total_seconds() - 1800) < 0.1
    assert abs((server_now - utcnow()).total_seconds()) < 2
    assert server_now >= before

    second = await client.post(f"/api/v1/quizzes/{quiz.id}/attempts")
    assert second.status_code == 200
    assert second.json()["id"] == payload["id"]
    assert second.json()["deadline_at"] == payload["deadline_at"]
    detail = await client.get(f"/api/v1/attempts/{payload['id']}")
    assert detail.status_code == 200
    assert detail.json()["deadline_at"] == payload["deadline_at"]
    assert abs((datetime.fromisoformat(detail.json()["server_now"]) - utcnow()).total_seconds()) < 2

    locked = await client.post(
        f"/api/v1/chats/{chat.json()['id']}/messages", json={"content": "考试中提问"}
    )
    assert locked.status_code == 403
    assert locked.json()["detail"] == "正式考试进行中，聊天功能已锁定"


async def test_deadline_rejects_answer_and_submit_before_writes(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kp = (await _add_learned_kps(db, course_id, 1))[0]
    quiz, questions = await _ready_quiz(db, course_id, kp)
    attempt = (await client.post(f"/api/v1/quizzes/{quiz.id}/attempts")).json()
    saved = await client.put(
        f"/api/v1/attempts/{attempt['id']}/answers/{questions[0].id}",
        json={"selected": ["A"]},
    )
    assert saved.status_code == 200
    row = await db.get(QuizAttempt, uuid.UUID(attempt["id"]))
    row.deadline_at = utcnow() - timedelta(seconds=30)
    await db.commit()

    rejected_save = await client.put(
        f"/api/v1/attempts/{attempt['id']}/answers/{questions[0].id}",
        json={"selected": ["B"]},
    )
    rejected_submit = await client.post(f"/api/v1/attempts/{attempt['id']}/submit")
    assert rejected_save.status_code == rejected_submit.status_code == 403
    assert rejected_save.json()["detail"] == "考试时间已到，答案已锁定"
    assert rejected_submit.json()["detail"] == "考试时间已到，答案已锁定"
    answer = (
        await db.execute(
            select(AttemptAnswer).where(AttemptAnswer.attempt_id == row.id)
        )
    ).scalar_one()
    await db.refresh(row)
    assert answer.answer == {"selected": ["A"]}
    assert row.status == "in_progress"


async def test_exam_events_validation_and_ownership(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kp = (await _add_learned_kps(db, course_id, 1))[0]
    formal, _ = await _ready_quiz(db, course_id, kp)
    daily, _ = await _ready_quiz(db, course_id, kp, kind="daily")
    formal_attempt = (await client.post(f"/api/v1/quizzes/{formal.id}/attempts")).json()
    daily_attempt = (await client.post(f"/api/v1/quizzes/{daily.id}/attempts")).json()

    accepted = await client.post(
        f"/api/v1/attempts/{formal_attempt['id']}/events", json={"kind": "tab_blur"}
    )
    assert accepted.status_code == 204
    listed = await client.get(f"/api/v1/attempts/{formal_attempt['id']}/events")
    assert listed.status_code == 200
    assert [item["kind"] for item in listed.json()] == ["tab_blur"]
    for kind in ("forced_submit", "unknown"):
        invalid = await client.post(
            f"/api/v1/attempts/{formal_attempt['id']}/events", json={"kind": kind}
        )
        assert invalid.status_code == 400
    nonformal = await client.post(
        f"/api/v1/attempts/{daily_attempt['id']}/events", json={"kind": "resume"}
    )
    assert nonformal.status_code == 400
    row = await db.get(QuizAttempt, uuid.UUID(formal_attempt["id"]))
    row.status = "submitted"
    await db.commit()
    submitted = await client.post(
        f"/api/v1/attempts/{formal_attempt['id']}/events", json={"kind": "resume"}
    )
    assert submitted.status_code == 400

    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "username": "student2",
            "password": "password123",
            "nickname": "小红",
            "invite_code": "TEST",
        },
    )
    assert registered.status_code == 200, registered.text
    cross_owner = await client.get(f"/api/v1/attempts/{formal_attempt['id']}/events")
    assert cross_owner.status_code == 404


async def test_expired_scan_forces_submit_grades_objective_and_is_idempotent(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kp = (await _add_learned_kps(db, course_id, 1))[0]
    quiz, questions = await _ready_quiz(db, course_id, kp, subjective=True)
    attempt = QuizAttempt(
        course_id=course_id,
        user_id=logged_in.id,
        quiz_id=quiz.id,
        kind="formal_exam",
        deadline_at=utcnow() - timedelta(seconds=1),
    )
    db.add(attempt)
    await db.flush()
    db.add(
        AttemptAnswer(
            attempt_id=attempt.id,
            question_id=questions[0].id,
            answer={"selected": ["B"]},
        )
    )
    await db.commit()

    assert await scan_expired_formal_exams(db) == 1
    await db.refresh(attempt)
    assert attempt.status == "submitted"
    assert attempt.auto_submitted is True
    answers = {
        answer.question_id: answer
        for answer in (
            await db.execute(select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id))
        ).scalars()
    }
    assert answers[questions[0].id].score == 0
    assert answers[questions[0].id].graded_by == "auto"
    assert answers[questions[1].id].answer == {"text": ""}
    grading_job = (
        await db.execute(
            select(Job).where(
                Job.kind == "grade_attempt",
                Job.payload["attempt_id"].astext == str(attempt.id),
            )
        )
    ).scalar_one()
    assert grading_job.status == "queued"
    events = list(
        (
            await db.execute(select(ExamEvent).where(ExamEvent.attempt_id == attempt.id))
        ).scalars()
    )
    assert [event.kind for event in events] == ["forced_submit"]
    assert await scan_expired_formal_exams(db) == 0
    assert (
        await db.execute(
            select(func.count()).select_from(ExamEvent).where(ExamEvent.attempt_id == attempt.id)
        )
    ).scalar_one() == 1


async def test_grading_tier_is_high_only_for_formal_exam(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
    monkeypatch,
) -> None:
    course = await _create_course(client)
    course_id = uuid.UUID(course["id"])
    kp = (await _add_learned_kps(db, course_id, 1))[0]
    tiers: list[str] = []

    async def fake_grade(**kwargs):
        tiers.append(kwargs["tier"].value)
        question_id = uuid.UUID(json.loads(kwargs["volatile"])[0]["question_id"])
        return GradeAttemptOut(
            items=[GradeItemOut(question_id=question_id, score=1, feedback_md="回答准确。")],
            weaknesses=[],
        )

    monkeypatch.setattr(grading_svc.runtime, "run_structured", fake_grade)
    for kind in ("formal_exam", "daily"):
        quiz, questions = await _ready_quiz(
            db, course_id, kp, kind=kind, subjective=True
        )
        attempt = QuizAttempt(
            course_id=course_id,
            user_id=logged_in.id,
            quiz_id=quiz.id,
            kind=kind,
            status="submitted",
            submitted_at=utcnow(),
        )
        db.add(attempt)
        await db.flush()
        db.add_all(
            [
                AttemptAnswer(
                    attempt_id=attempt.id,
                    question_id=questions[0].id,
                    answer={"selected": ["A"]},
                    is_correct=True,
                    score=1,
                    graded_by="auto",
                    graded_at=utcnow(),
                ),
                AttemptAnswer(
                    attempt_id=attempt.id,
                    question_id=questions[1].id,
                    answer={"text": "完整回答"},
                    graded_by="pending",
                ),
            ]
        )
        job = Job(
            kind="grade_attempt",
            payload={"attempt_id": str(attempt.id)},
            course_id=course_id,
            user_id=logged_in.id,
        )
        db.add(job)
        await db.commit()
        await grading_svc.handle_grade_attempt(db, job)

    assert tiers == ["high", "medium"]
