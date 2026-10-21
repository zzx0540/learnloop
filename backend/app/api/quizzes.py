import uuid
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.jobs.queue import enqueue
from app.models import AttemptAnswer, Course, ExamEvent, Job, Question, Quiz, QuizAttempt, User, utcnow
from app.schemas.course import JobOut
from app.schemas.exam import (
    AttemptAnswerOut,
    AttemptCreateOut,
    AttemptDetailOut,
    AttemptSummaryOut,
    BookExamIn,
    ExamEventCreate,
    ExamEventOut,
    QuestionPublicOut,
    QuestionResultOut,
    QuizListItemOut,
    QuizPublicOut,
    QuizResultOut,
    SubmitAttemptOut,
)
from app.services.grading import submit_attempt, validate_answer

router = APIRouter()
SHANGHAI = ZoneInfo("Asia/Shanghai")


def _attempt_summary(attempt: QuizAttempt | None) -> AttemptSummaryOut | None:
    return AttemptSummaryOut.model_validate(attempt) if attempt else None


def _answer_out(answer: AttemptAnswer) -> AttemptAnswerOut:
    return AttemptAnswerOut(
        id=answer.id,
        question_id=answer.question_id,
        answer=answer.answer,
        is_correct=answer.is_correct,
        score=answer.score,
        feedback_md=answer.feedback_md,
        graded_by=answer.graded_by,
        graded_at=answer.graded_at,
        updated_at=answer.updated_at,
    )


async def _owned_quiz(
    db: AsyncSession, quiz_id: uuid.UUID, user_id: uuid.UUID
) -> Quiz:
    quiz = await db.get(Quiz, quiz_id)
    course = await db.get(Course, quiz.course_id) if quiz else None
    if quiz is None or course is None or course.user_id != user_id:
        raise HTTPException(status_code=404, detail="试卷不存在")
    return quiz


async def _owned_attempt(
    db: AsyncSession,
    attempt_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    lock: bool = False,
) -> QuizAttempt:
    stmt = select(QuizAttempt).where(
        QuizAttempt.id == attempt_id, QuizAttempt.user_id == user_id
    )
    if lock:
        stmt = stmt.with_for_update()
    attempt = (await db.execute(stmt)).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status_code=404, detail="作答不存在")
    return attempt


async def _reusable_quiz_job(db: AsyncSession, dedupe_key: str) -> Job | None:
    return (
        await db.execute(
            select(Job)
            .where(
                Job.dedupe_key == dedupe_key,
                or_(
                    Job.status.in_(("queued", "running")),
                    exists(select(Quiz.id).where(Quiz.created_by_job_id == Job.id)),
                ),
            )
            .order_by(Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@router.get("/courses/{course_id}/quizzes", response_model=list[QuizListItemOut])
async def list_quizzes(
    kind: Literal["daily", "stage", "formal_exam"] | None = Query(default=None),
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[QuizListItemOut]:
    stmt = select(Quiz).where(Quiz.course_id == course.id)
    if kind:
        stmt = stmt.where(Quiz.kind == kind)
    quizzes = list(
        (await db.execute(stmt.order_by(Quiz.scheduled_date.desc(), Quiz.created_at.desc()))).scalars()
    )
    result = []
    for quiz in quizzes:
        question_count = (
            await db.execute(
                select(func.count()).select_from(Question).where(Question.quiz_id == quiz.id)
            )
        ).scalar_one()
        attempt = (
            await db.execute(
                select(QuizAttempt).where(
                    QuizAttempt.quiz_id == quiz.id,
                    QuizAttempt.user_id == course.user_id,
                )
            )
        ).scalar_one_or_none()
        result.append(
            QuizListItemOut(
                id=quiz.id,
                kind=quiz.kind,
                title=quiz.title,
                scheduled_date=quiz.scheduled_date,
                status=quiz.status,
                question_count=question_count,
                time_limit_seconds=quiz.time_limit_seconds,
                my_attempt=_attempt_summary(attempt),
            )
        )
    return result


@router.post("/courses/{course_id}/quizzes/generate-daily", response_model=JobOut)
async def generate_daily_quiz(
    course: Course = Depends(get_owned_course), db: AsyncSession = Depends(get_db)
) -> Job:
    quiz_date = datetime.now(SHANGHAI).date()
    dedupe_key = f"daily_quiz:{course.id}:{quiz_date.isoformat()}"
    job = await _reusable_quiz_job(db, dedupe_key)
    if job is None:
        job = await enqueue(
            db,
            "generate_quiz",
            {"course_id": str(course.id), "quiz_date": quiz_date.isoformat()},
            course_id=course.id,
            user_id=course.user_id,
            dedupe_key=dedupe_key,
        )
    if job is None:
        raise HTTPException(status_code=409, detail="试卷生成任务已存在")
    await db.commit()
    return job


@router.post("/courses/{course_id}/exams/book", response_model=JobOut)
async def book_formal_exam(
    data: BookExamIn,
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> Job:
    today = datetime.now(SHANGHAI).date()
    exam_date = data.exam_date or today
    if exam_date < today:
        raise HTTPException(status_code=400, detail="考试日期不能早于今天")
    dedupe_key = f"formal_exam:{course.id}:{exam_date.isoformat()}"
    job = await _reusable_quiz_job(db, dedupe_key)
    if job is None:
        job = await enqueue(
            db,
            "generate_quiz",
            {
                "course_id": str(course.id),
                "quiz_date": exam_date.isoformat(),
                "exam_kind": "formal_exam",
            },
            course_id=course.id,
            user_id=course.user_id,
            dedupe_key=dedupe_key,
        )
    if job is None:
        raise HTTPException(status_code=409, detail="正式考试生成任务已存在")
    await db.commit()
    return job


@router.get(
    "/quizzes/{quiz_id}",
    response_model=QuizPublicOut | QuizResultOut,
)
async def get_quiz(
    quiz_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> QuizPublicOut | QuizResultOut:
    quiz = await _owned_quiz(db, quiz_id, user.id)
    questions = list(
        (
            await db.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.seq)
            )
        ).scalars()
    )
    attempt = (
        await db.execute(
            select(QuizAttempt).where(
                QuizAttempt.quiz_id == quiz.id, QuizAttempt.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    common = {
        "id": quiz.id,
        "course_id": quiz.course_id,
        "kind": quiz.kind,
        "title": quiz.title,
        "scheduled_date": quiz.scheduled_date,
        "status": quiz.status,
        "plan_item_id": quiz.plan_item_id,
        "time_limit_seconds": quiz.time_limit_seconds,
    }
    if attempt is None or attempt.status not in ("submitted", "graded"):
        return QuizPublicOut(
            **common,
            my_attempt=_attempt_summary(attempt),
            questions=[
                QuestionPublicOut(
                    id=question.id,
                    seq=question.seq,
                    qtype=question.qtype,
                    stem_md=question.stem_md,
                    options=question.options,
                )
                for question in questions
            ],
        )
    answers = {
        answer.question_id: answer
        for answer in (
            await db.execute(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            )
        ).scalars()
    }
    return QuizResultOut(
        **common,
        my_attempt=_attempt_summary(attempt),
        questions=[
            QuestionResultOut(
                id=question.id,
                seq=question.seq,
                qtype=question.qtype,
                stem_md=question.stem_md,
                options=question.options,
                answer=question.answer,
                explanation_md=question.explanation_md,
                source_refs=question.source_refs,
                my_answer=_answer_out(answers[question.id]) if question.id in answers else None,
            )
            for question in questions
        ],
    )


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptCreateOut)
async def create_attempt(
    quiz_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttemptCreateOut:
    quiz = await _owned_quiz(db, quiz_id, user.id)
    if quiz.status != "ready":
        raise HTTPException(status_code=400, detail="试卷尚未就绪")
    attempt = (
        await db.execute(
            select(QuizAttempt).where(
                QuizAttempt.quiz_id == quiz.id, QuizAttempt.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if attempt is None:
        now = utcnow()
        deadline_at = None
        if quiz.kind == "formal_exam":
            if not quiz.time_limit_seconds:
                raise HTTPException(status_code=400, detail="正式考试未设置时限")
            deadline_at = now + timedelta(seconds=quiz.time_limit_seconds)
        attempt = QuizAttempt(
            course_id=quiz.course_id,
            user_id=user.id,
            quiz_id=quiz.id,
            kind=quiz.kind,
            started_at=now,
            deadline_at=deadline_at,
        )
        db.add(attempt)
        await db.commit()
    return AttemptCreateOut(
        **_attempt_summary(attempt).model_dump(),
        quiz_id=attempt.quiz_id,
        kind=attempt.kind,
        server_now=utcnow(),
    )


def _reject_expired_write(attempt: QuizAttempt) -> None:
    if attempt.deadline_at is not None and utcnow() > attempt.deadline_at:
        raise HTTPException(status_code=403, detail="考试时间已到，答案已锁定")


@router.put(
    "/attempts/{attempt_id}/answers/{question_id}", response_model=AttemptAnswerOut
)
async def save_attempt_answer(
    attempt_id: uuid.UUID,
    question_id: uuid.UUID,
    data: dict,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttemptAnswerOut:
    attempt = await _owned_attempt(db, attempt_id, user.id)
    _reject_expired_write(attempt)
    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="试卷已提交，不能修改答案")
    question = await db.get(Question, question_id)
    if question is None or question.quiz_id != attempt.quiz_id:
        raise HTTPException(status_code=404, detail="题目不存在")
    try:
        normalized = validate_answer(question, data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    answer = (
        await db.execute(
            select(AttemptAnswer).where(
                AttemptAnswer.attempt_id == attempt.id,
                AttemptAnswer.question_id == question.id,
            )
        )
    ).scalar_one_or_none()
    if answer is None:
        answer = AttemptAnswer(
            attempt_id=attempt.id, question_id=question.id, answer=normalized
        )
        db.add(answer)
    else:
        answer.answer = normalized
    await db.commit()
    await db.refresh(answer)
    return _answer_out(answer)


@router.post("/attempts/{attempt_id}/submit", response_model=SubmitAttemptOut)
async def submit_quiz_attempt(
    attempt_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubmitAttemptOut:
    attempt = await _owned_attempt(db, attempt_id, user.id, lock=True)
    _reject_expired_write(attempt)
    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="试卷已提交")
    try:
        job = await submit_attempt(db, attempt=attempt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.commit()
    return SubmitAttemptOut(
        **_attempt_summary(attempt).model_dump(),
        grading_job_id=job.id if job else None,
    )


@router.get("/attempts/{attempt_id}", response_model=AttemptDetailOut)
async def get_attempt(
    attempt_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AttemptDetailOut:
    attempt = await _owned_attempt(db, attempt_id, user.id)
    answers = list(
        (
            await db.execute(
                select(AttemptAnswer)
                .join(Question, Question.id == AttemptAnswer.question_id)
                .where(AttemptAnswer.attempt_id == attempt.id)
                .order_by(Question.seq)
            )
        ).scalars()
    )
    return AttemptDetailOut(
        **_attempt_summary(attempt).model_dump(),
        quiz_id=attempt.quiz_id,
        kind=attempt.kind,
        server_now=utcnow(),
        answers=[_answer_out(answer) for answer in answers],
    )


@router.post("/attempts/{attempt_id}/events", status_code=status.HTTP_204_NO_CONTENT)
async def create_exam_event(
    attempt_id: uuid.UUID,
    data: ExamEventCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    attempt = await _owned_attempt(db, attempt_id, user.id)
    if data.kind not in {"fullscreen_exit", "tab_blur", "resume"}:
        raise HTTPException(status_code=400, detail="考试事件类型不合法")
    if attempt.kind != "formal_exam" or attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="当前作答不接受考试事件")
    db.add(ExamEvent(attempt_id=attempt.id, kind=data.kind))
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/attempts/{attempt_id}/events", response_model=list[ExamEventOut])
async def list_exam_events(
    attempt_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ExamEvent]:
    attempt = await _owned_attempt(db, attempt_id, user.id)
    if attempt.kind != "formal_exam":
        raise HTTPException(status_code=400, detail="当前作答不是正式考试")
    return list(
        (
            await db.execute(
                select(ExamEvent)
                .where(ExamEvent.attempt_id == attempt.id)
                .order_by(ExamEvent.created_at, ExamEvent.id)
            )
        ).scalars()
    )
