from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db import get_db
from app.models import Course, PlanItem, User, UserDailyStat
from app.schemas.stats import CompletedItemOut, DailyStatOut, HeartbeatCreate, HeartbeatOut, StatsMeOut
from app.services.points import current_streak
from app.services.stats import record_heartbeat, shanghai_date

router = APIRouter()


@router.post("/stats/heartbeat", response_model=HeartbeatOut)
async def heartbeat(
    data: HeartbeatCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HeartbeatOut:
    course = await db.get(Course, data.course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="课程不存在")
    seconds = await record_heartbeat(db, user.id, course.id, data.kind)
    return HeartbeatOut(today_study_seconds=seconds)


@router.get("/stats/me", response_model=StatsMeOut)
async def stats_me(
    days: int = Query(default=7, ge=1, le=366),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StatsMeOut:
    today = shanghai_date()
    start_date = today - timedelta(days=days - 1)
    rows = list(
        (
            await db.execute(
                select(UserDailyStat)
                .where(UserDailyStat.user_id == user.id, UserDailyStat.date >= start_date)
                .order_by(UserDailyStat.date)
            )
        ).scalars()
    )
    return StatsMeOut(
        days=[
            DailyStatOut(
                date=row.date,
                study_seconds=row.study_seconds,
                review_count=row.review_count,
                learn_items_done=row.learn_items_done,
            )
            for row in rows
        ],
        streak=await current_streak(db, user.id, today),
    )


@router.get("/me/completed-items", response_model=list[CompletedItemOut])
async def completed_items(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[CompletedItemOut]:
    rows = (
        await db.execute(
            select(PlanItem, Course.name)
            .join(Course, Course.id == PlanItem.course_id)
            .where(
                Course.user_id == user.id,
                PlanItem.status == "done",
                PlanItem.completed_at.is_not(None),
            )
            .order_by(PlanItem.completed_at.desc(), PlanItem.id.desc())
            .limit(limit)
        )
    ).all()
    return [
        CompletedItemOut(
            title=item.title,
            type=item.type,
            course_name=course_name,
            completed_at=item.completed_at,
            actual_minutes=item.actual_minutes,
        )
        for item, course_name in rows
    ]
