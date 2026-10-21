import uuid
from datetime import timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttemptAnswer,
    KnowledgePoint,
    Question,
    Quiz,
    QuizAttempt,
    ReviewCard,
    User,
    WeaknessRecord,
    utcnow,
)
from app.services.mastery import evaluate_course_mastery


async def _add_evidence(
    db: AsyncSession,
    *,
    course_id: uuid.UUID,
    user_id: uuid.UUID,
    kp: KnowledgePoint,
    seq: int,
    score: float,
    qtype: str = "single_choice",
    kind: str = "daily",
) -> None:
    graded_at = utcnow() + timedelta(seconds=seq)
    quiz = Quiz(
        course_id=course_id,
        kind=kind,
        title=f"证据卷 {seq}",
        scheduled_date=graded_at.date(),
        status="ready",
    )
    db.add(quiz)
    await db.flush()
    question = Question(
        quiz_id=quiz.id,
        course_id=course_id,
        kp_id=kp.id,
        seq=1,
        qtype=qtype,
        stem_md="这是一道用于验证掌握状态机证据门槛的题目。",
        answer={"rubric": "标准", "reference_md": "参考"}
        if qtype == "short_answer"
        else {"correct_options": ["A"]},
        explanation_md="解析",
        source_refs=[{"wiki_slug": "mastery-source"}],
    )
    attempt = QuizAttempt(
        course_id=course_id,
        user_id=user_id,
        quiz_id=quiz.id,
        kind=kind,
        status="graded",
        submitted_at=graded_at,
        graded_at=graded_at,
        total_score=score,
        max_score=1,
    )
    db.add_all([question, attempt])
    await db.flush()
    db.add(
        AttemptAnswer(
            attempt_id=attempt.id,
            question_id=question.id,
            answer={"text": "作答"} if qtype == "short_answer" else {"selected": ["A"]},
            is_correct=None if qtype == "short_answer" else score >= 0.6,
            score=score,
            graded_by="llm" if qtype == "short_answer" else "auto",
            graded_at=graded_at,
        )
    )


def _card(course_id: uuid.UUID, kp: KnowledgePoint, stability: float, state: int = 2) -> ReviewCard:
    return ReviewCard(
        course_id=course_id,
        kp_id=kp.id,
        stability=stability,
        due=utcnow(),
        state=state,
        fsrs_card={},
    )


async def test_mastery_v2_evidence_matrix_regression_recovery_and_idempotency(
    client: AsyncClient,
    db: AsyncSession,
    logged_in: User,
) -> None:
    response = await client.post(
        "/api/v1/courses",
        json={"name": "掌握状态机课", "goal_text": "验证证据门槛", "daily_minutes": 30},
    )
    assert response.status_code == 200, response.text
    course_id = uuid.UUID(response.json()["id"])
    names = [
        "basic_ok",
        "no_correct",
        "wrong_state",
        "low_stability",
        "severe_weakness",
        "full_ok",
        "no_application",
        "bad_recent",
        "unresolved_weakness",
        "regress_basic",
        "regress_full",
        "recover_basic",
    ]
    kps = {
        name: KnowledgePoint(
            course_id=course_id,
            slug=name.replace("_", "-"),
            title=name,
            status=(
                "basic_mastered"
                if name == "regress_basic"
                else "fully_mastered"
                if name == "regress_full"
                else "needs_reinforce"
                if name == "recover_basic"
                else "learning"
            ),
        )
        for name in names
    }
    now = utcnow()
    kps["regress_basic"].basic_mastered_at = now - timedelta(days=2)
    kps["regress_full"].basic_mastered_at = now - timedelta(days=30)
    kps["regress_full"].fully_mastered_at = now - timedelta(days=2)
    kps["recover_basic"].basic_mastered_at = now - timedelta(days=5)
    db.add_all(kps.values())
    await db.flush()
    db.add_all(
        [
            _card(course_id, kps["basic_ok"], 3),
            _card(course_id, kps["no_correct"], 3),
            _card(course_id, kps["wrong_state"], 3, state=1),
            _card(course_id, kps["low_stability"], 2.9),
            _card(course_id, kps["severe_weakness"], 3),
            _card(course_id, kps["full_ok"], 21),
            _card(course_id, kps["no_application"], 21),
            _card(course_id, kps["bad_recent"], 21),
            _card(course_id, kps["unresolved_weakness"], 21),
            _card(course_id, kps["regress_basic"], 2.9),
            _card(course_id, kps["regress_full"], 20.9),
            _card(course_id, kps["recover_basic"], 3),
        ]
    )
    db.add_all(
        [
            WeaknessRecord(
                course_id=course_id,
                kp_id=kps["severe_weakness"].id,
                kind="concept_confusion",
                severity=2,
            ),
            WeaknessRecord(
                course_id=course_id,
                kp_id=kps["unresolved_weakness"].id,
                kind="expression_vague",
                severity=1,
                status="improving",
            ),
        ]
    )
    seq = 0

    async def evidence(name: str, score: float, **kwargs) -> None:
        nonlocal seq
        seq += 1
        await _add_evidence(
            db,
            course_id=course_id,
            user_id=logged_in.id,
            kp=kps[name],
            seq=seq,
            score=score,
            **kwargs,
        )

    for name in ("basic_ok", "wrong_state", "low_stability", "severe_weakness"):
        await evidence(name, 1)
    await evidence("recover_basic", 1)
    await evidence("full_ok", 1)
    await evidence("full_ok", 0.8, qtype="short_answer")
    await evidence("no_application", 1)
    await evidence("no_application", 1)
    await evidence("bad_recent", 1)
    await evidence("bad_recent", 0.8, qtype="short_answer")
    await evidence("bad_recent", 0)
    await evidence("unresolved_weakness", 1)
    await evidence("unresolved_weakness", 0.8, qtype="short_answer")
    await db.commit()

    result = await evaluate_course_mastery(db, course_id)
    await db.commit()
    assert result["evaluated"] == len(kps)
    expected = {
        "basic_ok": "basic_mastered",
        "no_correct": "learning",
        "wrong_state": "learning",
        "low_stability": "learning",
        "severe_weakness": "learning",
        "full_ok": "fully_mastered",
        "no_application": "basic_mastered",
        "bad_recent": "basic_mastered",
        "unresolved_weakness": "basic_mastered",
        "regress_basic": "needs_reinforce",
        "regress_full": "needs_reinforce",
        "recover_basic": "basic_mastered",
    }
    assert {name: kp.status for name, kp in kps.items()} == expected
    assert kps["basic_ok"].basic_mastered_at is not None
    assert kps["full_ok"].fully_mastered_at is not None

    snapshot = {
        name: (kp.status, kp.basic_mastered_at, kp.fully_mastered_at)
        for name, kp in kps.items()
    }
    await evaluate_course_mastery(db, course_id)
    await db.commit()
    assert {
        name: (kp.status, kp.basic_mastered_at, kp.fully_mastered_at)
        for name, kp in kps.items()
    } == snapshot
