import uuid
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import StudySession, UserDailyStat, utcnow

SHANGHAI = ZoneInfo("Asia/Shanghai")


def shanghai_date(now=None) -> date:
    return (now or utcnow()).astimezone(SHANGHAI).date()


async def increment_daily_stat(
    db: AsyncSession,
    user_id: uuid.UUID,
    stat_date: date,
    *,
    study_seconds: int = 0,
    review_count: int = 0,
    learn_items_done: int = 0,
) -> int:
    values = {
        "user_id": user_id,
        "date": stat_date,
        "study_seconds": study_seconds,
        "review_count": review_count,
        "learn_items_done": learn_items_done,
    }
    stmt = insert(UserDailyStat).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[UserDailyStat.user_id, UserDailyStat.date],
        set_={
            "study_seconds": UserDailyStat.study_seconds + stmt.excluded.study_seconds,
            "review_count": UserDailyStat.review_count + stmt.excluded.review_count,
            "learn_items_done": UserDailyStat.learn_items_done + stmt.excluded.learn_items_done,
        },
    )
    return (
        await db.execute(stmt.returning(UserDailyStat.study_seconds))
    ).scalar_one()


async def award_study_buckets(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    stat_date: date,
    previous_total: int,
    total: int,
) -> None:
    from app.services.points import POINTS_BASE, award_points

    first_bucket = previous_total // 600 + 1
    last_bucket = total // 600
    for bucket in range(first_bucket, last_bucket + 1):
        await award_points(
            db,
            user_id=user_id,
            course_id=course_id,
            kind="study_minutes",
            base_amount=POINTS_BASE["study_minutes"],
            note=f"{stat_date.isoformat()}:{bucket}",
            point_date=stat_date,
        )


async def record_heartbeat(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID, kind: str
) -> int:
    now = utcnow()
    session = (
        await db.execute(
            select(StudySession)
            .where(
                StudySession.user_id == user_id,
                StudySession.course_id == course_id,
                StudySession.kind == kind,
                StudySession.last_beat_at > now - timedelta(minutes=3),
            )
            .order_by(StudySession.last_beat_at.desc())
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    delta = 0
    if session is None:
        db.add(
            StudySession(
                user_id=user_id,
                course_id=course_id,
                kind=kind,
                started_at=now,
                last_beat_at=now,
            )
        )
    else:
        delta = max(0, min(180, int((now - session.last_beat_at).total_seconds())))
        session.seconds += delta
        session.last_beat_at = now
    today = shanghai_date(now)
    if delta > 0:
        total = await increment_daily_stat(db, user_id, today, study_seconds=delta)
        await award_study_buckets(
            db,
            user_id=user_id,
            course_id=course_id,
            stat_date=today,
            previous_total=total - delta,
            total=total,
        )
    await db.commit()
    return (
        await db.execute(
            select(UserDailyStat.study_seconds).where(
                UserDailyStat.user_id == user_id,
                UserDailyStat.date == today,
            )
        )
    ).scalar_one_or_none() or 0
