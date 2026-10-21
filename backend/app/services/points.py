import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PointsLedger, Quiz, QuizAttempt, UserDailyStat

SHANGHAI = ZoneInfo("Asia/Shanghai")
POINTS_BASE = {
    "study_minutes": 5,
    "item_done": 10,
    "review_done": 2,
}


def multiplier(streak_including_today: int) -> float:
    return 1.0 + 0.1 * min(streak_including_today, 10)


def shanghai_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, SHANGHAI).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


async def current_streak(db: AsyncSession, user_id: uuid.UUID, today: date) -> int:
    active_dates = set(
        (
            await db.execute(
                select(UserDailyStat.date).where(
                    UserDailyStat.user_id == user_id,
                    UserDailyStat.date <= today,
                    or_(
                        UserDailyStat.study_seconds > 0,
                        UserDailyStat.review_count > 0,
                        UserDailyStat.learn_items_done > 0,
                    ),
                )
            )
        ).scalars()
    )
    cursor = today if today in active_dates else today - timedelta(days=1)
    streak = 0
    while cursor in active_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


async def is_active_day(db: AsyncSession, user_id: uuid.UUID, day: date) -> bool:
    return (
        await db.execute(
            select(UserDailyStat.user_id).where(
                UserDailyStat.user_id == user_id,
                UserDailyStat.date == day,
                or_(
                    UserDailyStat.study_seconds > 0,
                    UserDailyStat.review_count > 0,
                    UserDailyStat.learn_items_done > 0,
                ),
            )
        )
    ).scalar_one_or_none() is not None


async def add_ledger_entry(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
    kind: str,
    amount: int,
    applied_multiplier: float,
    note: str,
    ref_id: uuid.UUID | None = None,
) -> PointsLedger | None:
    ledger = PointsLedger(
        user_id=user_id,
        course_id=course_id,
        kind=kind,
        amount=amount,
        multiplier=applied_multiplier,
        note=note,
        ref_id=ref_id,
    )
    try:
        async with db.begin_nested():
            db.add(ledger)
            await db.flush()
    except IntegrityError:
        return None
    return ledger


async def award_points(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID | None,
    kind: str,
    base_amount: int,
    note: str,
    point_date: date,
    ref_id: uuid.UUID | None = None,
) -> PointsLedger | None:
    applied_multiplier = multiplier(await current_streak(db, user_id, point_date))
    return await add_ledger_entry(
        db,
        user_id=user_id,
        course_id=course_id,
        kind=kind,
        amount=round(base_amount * applied_multiplier),
        applied_multiplier=applied_multiplier,
        note=note,
        ref_id=ref_id,
    )


async def award_quiz_points(db: AsyncSession, attempt: QuizAttempt) -> PointsLedger | None:
    if attempt.quiz_id is None:
        return None
    quiz = await db.get(Quiz, attempt.quiz_id)
    if quiz is None:
        return None
    maximum = attempt.max_score or 0
    score_rate = (attempt.total_score or 0) / maximum if maximum > 0 else 0
    base_amount = round(score_rate * (20 if quiz.kind == "daily" else 40))
    point_date = (attempt.graded_at or datetime.now(timezone.utc)).astimezone(SHANGHAI).date()
    return await award_points(
        db,
        user_id=attempt.user_id,
        course_id=attempt.course_id,
        kind="quiz_score",
        base_amount=base_amount,
        note=f"attempt:{attempt.id}",
        point_date=point_date,
        ref_id=attempt.id,
    )


async def points_balance(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                select(func.coalesce(func.sum(PointsLedger.amount), 0)).where(
                    PointsLedger.user_id == user_id
                )
            )
        ).scalar_one()
    )
