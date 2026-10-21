import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_owned_course
from app.db import get_db
from app.models import Course, TangentItem, User
from app.schemas.tangent import TangentOut, TangentStatus, TangentUpdate

router = APIRouter()


@router.get("/courses/{course_id}/tangents", response_model=list[TangentOut])
async def list_tangents(
    status: TangentStatus | None = Query(default=None),
    course: Course = Depends(get_owned_course),
    db: AsyncSession = Depends(get_db),
) -> list[TangentItem]:
    stmt = select(TangentItem).where(TangentItem.course_id == course.id)
    if status is not None:
        stmt = stmt.where(TangentItem.status == status)
    return list((await db.execute(stmt.order_by(TangentItem.created_at.desc()))).scalars())


@router.patch("/tangents/{tangent_id}", response_model=TangentOut)
async def update_tangent(
    tangent_id: uuid.UUID,
    data: TangentUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TangentItem:
    tangent = await db.get(TangentItem, tangent_id)
    if tangent is None:
        raise HTTPException(status_code=404, detail="拓展条目不存在")
    course = await db.get(Course, tangent.course_id)
    if course is None or course.user_id != user.id:
        raise HTTPException(status_code=404, detail="拓展条目不存在")
    tangent.status = data.status
    await db.commit()
    return tangent
