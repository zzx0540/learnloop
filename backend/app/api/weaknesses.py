from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_owned_course
from app.db import get_db
from app.models import Course, KnowledgePoint, WeaknessRecord
from app.schemas.exam import WeaknessOut

router = APIRouter()


@router.get("/courses/{course_id}/weaknesses", response_model=list[WeaknessOut])
async def list_weaknesses(
    status: Literal["open", "improving", "resolved"] | None = Query(default=None),
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[WeaknessOut]:
    stmt = (
        select(WeaknessRecord, KnowledgePoint)
        .join(KnowledgePoint, KnowledgePoint.id == WeaknessRecord.kp_id)
        .where(WeaknessRecord.course_id == course.id)
    )
    if status:
        stmt = stmt.where(WeaknessRecord.status == status)
    rows = (
        await db.execute(stmt.order_by(WeaknessRecord.last_seen_at.desc()))
    ).all()
    return [
        WeaknessOut(
            id=record.id,
            kp_id=kp.id,
            kp_slug=kp.slug,
            kp_title=kp.title,
            kind=record.kind,
            severity=record.severity,
            status=record.status,
            seen_count=record.seen_count,
            correct_streak=record.correct_streak,
            description=record.description,
            last_seen_at=record.last_seen_at,
            created_at=record.created_at,
        )
        for record, kp in rows
    ]
