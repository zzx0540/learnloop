import math
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fsrs import FSRS, Card, Rating
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AttemptAnswer,
    KnowledgePoint,
    Question,
    Quiz,
    QuizAttempt,
    ReviewCard,
    WeaknessRecord,
    utcnow,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def simulate_mastery_dates(learn_date: date) -> tuple[date, date]:
    review_at = datetime.combine(learn_date, time(9), SHANGHAI).astimezone(timezone.utc)
    scheduler = FSRS(request_retention=0.9)
    card = Card(due=review_at)
    basic_at: date | None = None
    full_at: date | None = None

    for _ in range(100):
        info = scheduler.repeat(card, review_at)[Rating.Good]
        card = info.card
        review_date = review_at.astimezone(SHANGHAI).date()
        if basic_at is None and card.stability >= 3:
            basic_at = max(learn_date + timedelta(days=1), review_date)
        if card.stability >= 21:
            full_at = max(basic_at or learn_date + timedelta(days=1), review_date)
            break
        review_at = card.due

    if basic_at is None or full_at is None:
        raise RuntimeError("FSRS 掌握日期模拟未在迭代上限内收敛")
    return basic_at, full_at


def simulate_from_card(card: Card, today: date) -> tuple[date, date]:
    if card.stability >= 21:
        return today, today
    scheduler = FSRS(request_retention=0.9)
    current = Card.from_dict(card.to_dict())
    now = datetime.combine(today, time.min, SHANGHAI).astimezone(timezone.utc)
    review_at = max(current.due, now)
    basic_at: date | None = today if current.stability >= 3 else None

    for _ in range(100):
        info = scheduler.repeat(current, review_at)[Rating.Good]
        current = info.card
        review_date = review_at.astimezone(SHANGHAI).date()
        if basic_at is None and current.stability >= 3:
            basic_at = max(today, review_date)
        if current.stability >= 21:
            full_at = max(basic_at or today, review_date)
            return basic_at or today, full_at
        review_at = current.due
    raise RuntimeError("FSRS 真实卡掌握日期模拟未在迭代上限内收敛")


def aggregate_course_date(
    projections: list[tuple[date | None, int]], required_ratio: float
) -> date | None:
    if not projections:
        return None
    required_count = math.ceil(len(projections) * required_ratio)
    available = sorted(projected for projected, _ in projections if projected is not None)
    if len(available) < required_count:
        return None
    important = [projected for projected, importance in projections if importance >= 4]
    if any(projected is None for projected in important):
        return None
    threshold_date = available[required_count - 1]
    important_date = max((projected for projected in important if projected is not None), default=None)
    return max(threshold_date, important_date) if important_date else threshold_date


async def evaluate_course_mastery(db: AsyncSession, course_id: uuid.UUID) -> dict:
    kps = list(
        (
            await db.execute(
                select(KnowledgePoint).where(
                    KnowledgePoint.course_id == course_id,
                    KnowledgePoint.status != "archived",
                )
            )
        ).scalars()
    )
    cards = {
        card.kp_id: card
        for card in (
            await db.execute(
                select(ReviewCard).where(
                    ReviewCard.course_id == course_id,
                    ReviewCard.kp_id.is_not(None),
                )
            )
        ).scalars()
    }
    now = utcnow()
    changed = 0
    for kp in kps:
        answer_rows = (
            await db.execute(
                select(
                    AttemptAnswer.score,
                    AttemptAnswer.is_correct,
                    AttemptAnswer.graded_at,
                    AttemptAnswer.updated_at,
                    Question.qtype,
                    Quiz.kind,
                )
                .join(Question, Question.id == AttemptAnswer.question_id)
                .join(QuizAttempt, QuizAttempt.id == AttemptAnswer.attempt_id)
                .join(Quiz, Quiz.id == Question.quiz_id)
                .where(
                    Question.kp_id == kp.id,
                    QuizAttempt.status == "graded",
                    AttemptAnswer.score.is_not(None),
                )
                .order_by(
                    AttemptAnswer.graded_at.desc().nullslast(),
                    AttemptAnswer.updated_at.desc(),
                )
            )
        ).all()
        correct_rows = [
            row
            for row in answer_rows
            if row.is_correct is True or (row.score is not None and row.score >= 0.6)
        ]
        application_count = sum(
            row.score is not None
            and row.score >= 0.7
            and (row.kind == "stage" or row.qtype == "short_answer")
            for row in answer_rows
        )
        recent = answer_rows[:3]
        recent_rate = (
            sum(
                row.is_correct is True or (row.score is not None and row.score >= 0.6)
                for row in recent
            )
            / len(recent)
            if recent
            else 0.0
        )
        severe_open = (
            await db.execute(
                select(WeaknessRecord.id)
                .where(
                    WeaknessRecord.course_id == course_id,
                    WeaknessRecord.kp_id == kp.id,
                    WeaknessRecord.status == "open",
                    WeaknessRecord.severity >= 2,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        any_unresolved = (
            await db.execute(
                select(WeaknessRecord.id)
                .where(
                    WeaknessRecord.course_id == course_id,
                    WeaknessRecord.kp_id == kp.id,
                    WeaknessRecord.status != "resolved",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        card = cards.get(kp.id)
        stability = card.stability if card is not None else 0.0
        basic_ready = (
            card is not None
            and card.state == 2
            and stability >= 3
            and len(correct_rows) >= 1
            and severe_open is None
        )
        full_ready = (
            stability >= 21
            and len(correct_rows) >= 2
            and application_count >= 1
            and recent_rate >= 0.8
            and any_unresolved is None
        )

        old_status = kp.status
        if kp.status == "fully_mastered" and stability < 21:
            kp.status = "needs_reinforce"
        elif kp.status == "basic_mastered" and stability < 3:
            kp.status = "needs_reinforce"
        elif full_ready:
            if kp.basic_mastered_at is None:
                kp.basic_mastered_at = now
            if kp.fully_mastered_at is None:
                kp.fully_mastered_at = now
            kp.status = "fully_mastered"
            kp.mastery_score = 100
        elif basic_ready:
            if kp.basic_mastered_at is None:
                kp.basic_mastered_at = now
            kp.status = "basic_mastered"
            kp.mastery_score = max(kp.mastery_score, 60)
        if kp.status == "needs_reinforce":
            kp.mastery_score = min(kp.mastery_score, 59)
        changed += kp.status != old_status
    return {"evaluated": len(kps), "changed": changed}
