import json
import math
import re
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.runtime import Tier, runtime
from app.jobs.queue import enqueue
from app.jobs.worker import job_handler
from app.models import (
    AttemptAnswer,
    Job,
    KnowledgePoint,
    PlanItem,
    Question,
    Quiz,
    QuizAttempt,
    utcnow,
)
from app.services.mastery import evaluate_course_mastery
from app.services.points import award_quiz_points
from app.services.weakness import (
    AnswerEvidence,
    WeaknessEvidence,
    apply_answer_evidence,
    record_weaknesses,
)

GRADING_SYSTEM_V1 = """# 陪伴反馈师批改器 v1

依据每道主观题的 rubric 与 reference_md 给 0-1 分并写具体反馈；同时只对主观低分题和客观错题归因薄弱类型。
不得改变客观题分数，不得为本卷以外的知识点创建薄弱归因。只输出 JSON。"""


class GradeItemOut(BaseModel):
    question_id: uuid.UUID
    score: float = Field(ge=0, le=1)
    feedback_md: str = Field(min_length=1)


class WeaknessItemOut(BaseModel):
    kp_slug: str
    kind: str = Field(
        pattern=(
            r"^(concept_confusion|memory_lapse|application_gap|calculation_error|"
            r"expression_vague)$"
        )
    )
    severity: int = Field(ge=1, le=3)
    description: str = Field(max_length=200)


class GradeAttemptOut(BaseModel):
    items: list[GradeItemOut]
    weaknesses: list[WeaknessItemOut]


def normalize_cloze(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).lower()


def validate_answer(question: Question, answer: dict) -> dict:
    if question.qtype in ("single_choice", "multi_choice"):
        if set(answer) != {"selected"} or not isinstance(answer["selected"], list):
            raise ValueError("选项作答格式不正确")
        if any(not isinstance(key, str) for key in answer["selected"]):
            raise ValueError("选项作答格式不正确")
        selected = list(dict.fromkeys(answer["selected"]))
        valid_keys = {option["key"] for option in question.options}
        if any(key not in valid_keys for key in selected):
            raise ValueError("作答包含不存在的选项")
        if question.qtype == "single_choice" and len(selected) > 1:
            raise ValueError("单选题最多选择一个选项")
        return {"selected": selected}
    if question.qtype == "true_false":
        if set(answer) != {"selected_bool"} or not isinstance(answer["selected_bool"], bool):
            raise ValueError("判断题作答格式不正确")
        return {"selected_bool": answer["selected_bool"]}
    if question.qtype in ("cloze", "short_answer"):
        if set(answer) != {"text"} or not isinstance(answer["text"], str):
            raise ValueError("文本作答格式不正确")
        return {"text": answer["text"]}
    raise ValueError("未知题型")


def empty_answer(qtype: str) -> dict:
    if qtype in ("single_choice", "multi_choice"):
        return {"selected": []}
    if qtype == "true_false":
        return {"selected_bool": None}
    return {"text": ""}


def grade_objective(question: Question, answer: dict) -> bool:
    if question.qtype in ("single_choice", "multi_choice"):
        return set(answer.get("selected", [])) == set(question.answer["correct_options"])
    if question.qtype == "true_false":
        return answer.get("selected_bool") is question.answer["correct_bool"]
    if question.qtype == "cloze":
        submitted = normalize_cloze(answer.get("text", ""))
        return bool(submitted) and any(
            submitted == normalize_cloze(accepted)
            for accepted in question.answer["accept_texts"]
        )
    raise ValueError("主观题不能自动判分")


async def submit_attempt(
    db: AsyncSession,
    *,
    attempt: QuizAttempt,
) -> Job | None:
    if attempt.quiz_id is None:
        raise ValueError("作答未关联试卷")
    quiz = await db.get(Quiz, attempt.quiz_id)
    if quiz is None:
        raise ValueError("试卷不存在")
    questions = list(
        (
            await db.execute(
                select(Question).where(Question.quiz_id == quiz.id).order_by(Question.seq)
            )
        ).scalars()
    )
    existing = {
        answer.question_id: answer
        for answer in (
            await db.execute(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            )
        ).scalars()
    }
    now = utcnow()
    answers: list[AttemptAnswer] = []
    has_subjective = False
    for question in questions:
        answer = existing.get(question.id)
        if answer is None:
            answer = AttemptAnswer(
                attempt_id=attempt.id,
                question_id=question.id,
                answer=empty_answer(question.qtype),
            )
            db.add(answer)
        if question.qtype == "short_answer":
            has_subjective = True
            answer.is_correct = None
            answer.score = None
            answer.graded_by = "pending"
            answer.graded_at = None
        else:
            answer.is_correct = grade_objective(question, answer.answer)
            answer.score = 1.0 if answer.is_correct else 0.0
            answer.graded_by = "auto"
            answer.graded_at = now
        answers.append(answer)
    await db.flush()
    attempt.status = "submitted"
    attempt.submitted_at = now
    attempt.total_score = sum(answer.score or 0 for answer in answers)
    attempt.max_score = float(len(questions))

    if quiz.plan_item_id is not None:
        item = await db.get(PlanItem, quiz.plan_item_id)
        if item is not None and item.status in ("pending", "in_progress"):
            item.status = "done"
            item.completed_at = now
            elapsed = max(0, (now - attempt.started_at).total_seconds())
            item.actual_minutes = min(240, math.floor(elapsed / 60))

    if has_subjective:
        return await enqueue(
            db,
            "grade_attempt",
            {"attempt_id": str(attempt.id)},
            course_id=attempt.course_id,
            user_id=attempt.user_id,
            dedupe_key=f"grade:{attempt.id}",
        )
    await finalize_without_llm(
        db,
        attempt=attempt,
        answers=answers,
        questions={question.id: question for question in questions},
    )
    return None


async def finalize_without_llm(
    db: AsyncSession,
    *,
    attempt: QuizAttempt,
    answers: list[AttemptAnswer],
    questions: dict[uuid.UUID, Question],
) -> None:
    now = utcnow()
    attempt.status = "graded"
    attempt.graded_at = now
    attempt.total_score = sum(answer.score or 0 for answer in answers)
    attempt.max_score = float(len(answers))
    await apply_answer_evidence(
        db,
        course_id=attempt.course_id,
        items=[
            AnswerEvidence(
                kp_id=questions[answer.question_id].kp_id,
                correct=answer.is_correct is True or (answer.score or 0) >= 0.6,
            )
            for answer in answers
        ],
    )
    await evaluate_course_mastery(db, attempt.course_id)
    await award_quiz_points(db, attempt)


def _grading_input(
    questions: list[Question], answers: dict[uuid.UUID, AttemptAnswer]
) -> str:
    rows = []
    for question in questions:
        answer = answers[question.id]
        if question.qtype == "short_answer":
            rows.append(
                {
                    "question_id": str(question.id),
                    "kp_id": str(question.kp_id),
                    "qtype": question.qtype,
                    "stem_md": question.stem_md,
                    "rubric": question.answer["rubric"],
                    "reference_md": question.answer["reference_md"],
                    "student_answer": answer.answer,
                }
            )
        elif answer.is_correct is False:
            rows.append(
                {
                    "question_id": str(question.id),
                    "kp_id": str(question.kp_id),
                    "qtype": question.qtype,
                    "stem_md": question.stem_md,
                    "correct_answer": question.answer,
                    "student_answer": answer.answer,
                }
            )
    return json.dumps(rows, ensure_ascii=False)


@job_handler("grade_attempt")
async def handle_grade_attempt(db: AsyncSession, job: Job) -> dict:
    attempt_id = uuid.UUID(job.payload["attempt_id"])
    attempt = (
        await db.execute(
            select(QuizAttempt).where(QuizAttempt.id == attempt_id).with_for_update()
        )
    ).scalar_one_or_none()
    if attempt is None:
        return {"skipped": "作答不存在"}
    if attempt.status == "graded":
        return {"skipped": "作答已批改"}
    if attempt.status != "submitted" or attempt.quiz_id is None:
        return {"skipped": "作答尚未提交"}
    quiz = await db.get(Quiz, attempt.quiz_id)
    questions = list(
        (
            await db.execute(
                select(Question)
                .where(Question.quiz_id == attempt.quiz_id)
                .order_by(Question.seq)
            )
        ).scalars()
    )
    answer_rows = list(
        (
            await db.execute(
                select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id)
            )
        ).scalars()
    )
    answers = {answer.question_id: answer for answer in answer_rows}
    subjective = [question for question in questions if question.qtype == "short_answer"]
    if not subjective:
        await finalize_without_llm(
            db,
            attempt=attempt,
            answers=answer_rows,
            questions={question.id: question for question in questions},
        )
        await db.commit()
        return {"attempt_id": str(attempt.id), "score": attempt.total_score}

    kps = list(
        (
            await db.execute(
                select(KnowledgePoint).where(
                    KnowledgePoint.id.in_([question.kp_id for question in questions])
                )
            )
        ).scalars()
    )
    kp_by_slug = {kp.slug: kp for kp in kps}
    output: GradeAttemptOut = await runtime.run_structured(
        agent="companion",
        operation="grade_attempt",
        tier=Tier.high if quiz and quiz.kind == "formal_exam" else Tier.medium,
        response_model=GradeAttemptOut,
        system_prompt=GRADING_SYSTEM_V1,
        stable_blocks=[
            "# 本卷知识点索引\n"
            + "\n".join(f"{kp.slug}: {kp.title}" for kp in sorted(kps, key=lambda x: x.slug))
        ],
        volatile=_grading_input(questions, answers),
        course_id=attempt.course_id,
        job_id=job.id,
        max_tokens=8192,
        temperature=0.2,
        thinking=False,
    )
    valid_subjective_ids = {question.id for question in subjective}
    grade_items: dict[uuid.UUID, GradeItemOut] = {}
    for item in output.items:
        if item.question_id in valid_subjective_ids and item.question_id not in grade_items:
            grade_items[item.question_id] = item
    now = utcnow()
    eligible_weak_kp_ids = {
        question.kp_id
        for question in questions
        if question.qtype != "short_answer" and answers[question.id].is_correct is False
    }
    for question in subjective:
        answer = answers[question.id]
        item = grade_items.get(question.id)
        answer.score = item.score if item else 0.0
        answer.feedback_md = item.feedback_md if item else "批改结果缺失，本题按 0 分计。"
        answer.graded_by = "llm"
        answer.graded_at = now
        if answer.score < 0.6:
            eligible_weak_kp_ids.add(question.kp_id)

    weakness_by_key: dict[tuple[str, str], WeaknessItemOut] = {}
    for item in output.weaknesses:
        kp = kp_by_slug.get(item.kp_slug)
        if kp is None or kp.id not in eligible_weak_kp_ids:
            continue
        key = (item.kp_slug, item.kind)
        previous = weakness_by_key.get(key)
        if previous is None or item.severity > previous.severity:
            weakness_by_key[key] = item
    await record_weaknesses(
        db,
        course_id=attempt.course_id,
        items=[
            WeaknessEvidence(
                kp_id=kp_by_slug[item.kp_slug].id,
                kind=item.kind,
                severity=item.severity,
                description=item.description,
            )
            for item in weakness_by_key.values()
        ],
    )
    attempt.status = "graded"
    attempt.graded_at = now
    attempt.total_score = sum(answer.score or 0 for answer in answer_rows)
    attempt.max_score = float(len(questions))
    await apply_answer_evidence(
        db,
        course_id=attempt.course_id,
        items=[
            AnswerEvidence(
                kp_id=question.kp_id,
                correct=(
                    answers[question.id].is_correct is True
                    or (answers[question.id].score or 0) >= 0.6
                ),
            )
            for question in questions
        ],
    )
    await evaluate_course_mastery(db, attempt.course_id)
    await award_quiz_points(db, attempt)
    await db.commit()
    return {
        "attempt_id": str(attempt.id),
        "score": attempt.total_score,
        "max_score": attempt.max_score,
        "weaknesses": len(weakness_by_key),
        "quiz_kind": quiz.kind if quiz else None,
    }
